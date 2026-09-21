#!/usr/bin/env bash
# Post-install for a standalone OpenShell gateway used by ABEvalFlow.
#
# A freshly installed gateway cannot yet be driven by the pipeline. Three things
# are missing, and each produces a distinct, confusing failure:
#
#   1. the CI client has no mTLS identity that this gateway's CA trusts
#      -> "received fatal alert: CertificateRequired"
#   2. the CI OIDC subject is not a member of the gateway workspace
#      -> "The caller does not have permission to execute the specified operation"
#   3. the gateway has no inference route
#      -> "cluster inference is not configured" inside the sandbox
#
# This script fixes all three. It runs a Job in the namespace (the VM is only
# reachable through the Kubernetes API, via virtctl) and prints nothing secret.
#
# Usage:
#   NS=<namespace> SANDBOX=<helm-release> \
#   MODEL=rits/zai-org/glm-5-3 \
#   [LITELLM_URL=http://litellm:4000/v1] [SUBJECT=<oidc-sub>] \
#     ./config/forge-saw/gateway-postinstall.sh
#
# SUBJECT is the OIDC subject the pipeline authenticates as. Leave it unset to
# skip the membership step and add it later; `openshell whoami` in the evaluate
# step log prints the value, as does the gateway's own error message.
set -euo pipefail

NS="${NS:?set NS to the target namespace}"
SANDBOX="${SANDBOX:?set SANDBOX to the openshell-saw helm release name}"
MODEL="${MODEL:?set MODEL to the model id served by your endpoint}"
LITELLM_URL="${LITELLM_URL:-http://litellm.${NS}.svc.cluster.local:4000/v1}"
SUBJECT="${SUBJECT:-}"
SSH_SECRET="${SSH_SECRET:-openshell-aap-ssh}"
# Sandbox image the evaluate step will ask the gateway to run. The chart
# pre-pulls only the supervisor image and then deletes the VM's registry
# credentials, so a private agent image cannot be pulled later: sandbox create
# fails with "unable to retrieve auth token: invalid username/password".
SANDBOX_IMAGE="${SANDBOX_IMAGE:-}"
REGISTRY_SECRET="${REGISTRY_SECRET:-ghcr-rh-forge-auth}"
SETUP_IMAGE="${SETUP_IMAGE:-ghcr.io/rh-forge/forge-saw-setup:latest}"
JOB="${SANDBOX}-postinstall"

LITELLM_HOST="$(printf '%s' "$LITELLM_URL" | sed -E 's#^https?://##; s#[:/].*$##')"
LITELLM_PORT="$(printf '%s' "$LITELLM_URL" | sed -nE 's#^https?://[^:/]+:([0-9]+).*$#\1#p')"
LITELLM_PORT="${LITELLM_PORT:-4000}"

echo "namespace=$NS sandbox=$SANDBOX model=$MODEL endpoint=$LITELLM_URL"

# The provider profile is data, not a script: ship it through a ConfigMap so the
# Job can import it without a nested heredoc.
oc create configmap "${SANDBOX}-provider-profile" -n "$NS" --dry-run=client -o yaml \
  --from-literal=profile.yaml="$(cat <<PROF
id: litellm-local
display_name: Namespace LiteLLM
description: OpenAI-compatible inference endpoint in this namespace
category: inference
credentials:
  - name: api_key
    description: proxy key (any value when the proxy does not check it)
    env_vars:
      - OPENAI_API_KEY
    required: true
    auth_style: bearer
    header_name: authorization
    query_param: ""
endpoints:
  - host: ${LITELLM_HOST}
    port: ${LITELLM_PORT}
    protocol: rest
    access: read-write
    enforcement: enforce
binaries:
  - /usr/bin/curl
  - /usr/local/bin/curl
inference_capable: true
discovery:
  credentials:
    - api_key
PROF
)" | oc apply -f - >/dev/null

# The chart's setup ServiceAccount can only read five named Secrets, so it
# cannot create openshell-mtls. Give this Job its own identity: the chart's Role
# for VM access, plus the one Secret it has to manage.
oc apply -n "$NS" -f - <<RBAC >/dev/null
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ${SANDBOX}-postinstall
automountServiceAccountToken: false
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ${SANDBOX}-postinstall
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["openshell-mtls"]
    verbs: ["get", "create", "update", "patch"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["create"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${SANDBOX}-postinstall
subjects:
  - kind: ServiceAccount
    name: ${SANDBOX}-postinstall
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ${SANDBOX}-postinstall
---
# reuse the chart's Role for the VirtualMachine subresources virtctl needs
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ${SANDBOX}-postinstall-vm
subjects:
  - kind: ServiceAccount
    name: ${SANDBOX}-postinstall
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: ${SANDBOX}-setup
RBAC

oc delete job "$JOB" -n "$NS" --ignore-not-found >/dev/null 2>&1 || true

oc apply -n "$NS" -f - <<JOBYAML >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  labels:
    app.kubernetes.io/part-of: openshell-cnv-fedora
spec:
  backoffLimit: 2
  template:
    metadata:
      labels:
        app.kubernetes.io/part-of: openshell-cnv-fedora
    spec:
      restartPolicy: Never
      serviceAccountName: ${SANDBOX}-postinstall
      imagePullSecrets: [{name: ghcr-rh-forge-auth}]
      # The chart's setup ServiceAccount sets automountServiceAccountToken: false,
      # so a pod using it gets no API credentials and virtctl falls back to
      # localhost ("dial tcp [::1]:8080: connect: connection refused"). Mount the
      # projected token the same way the chart's own setup Job does.
      automountServiceAccountToken: false
      volumes:
        - name: setup-api-credentials
          projected:
            defaultMode: 0440
            sources:
              - serviceAccountToken:
                  path: token
                  expirationSeconds: 900
              - configMap:
                  name: kube-root-ca.crt
                  items:
                    - key: ca.crt
                      path: ca.crt
              - downwardAPI:
                  items:
                    - path: namespace
                      fieldRef:
                        fieldPath: metadata.namespace
        - name: ssh-key
          secret: {secretName: ${SSH_SECRET}, defaultMode: 0400}
        - name: registry-auth
          secret:
            secretName: ${REGISTRY_SECRET}
            optional: true
            items:
              - key: .dockerconfigjson
                path: auth.json
        - name: profile
          configMap: {name: ${SANDBOX}-provider-profile}
      containers:
        - name: postinstall
          image: ${SETUP_IMAGE}
          volumeMounts:
            - {name: setup-api-credentials, mountPath: /var/run/secrets/kubernetes.io/serviceaccount, readOnly: true}
            - {name: ssh-key, mountPath: /ssh-key, readOnly: true}
            - {name: registry-auth, mountPath: /registry-auth, readOnly: true}
            - {name: profile, mountPath: /profile, readOnly: true}
          env:
            - {name: NS, value: "${NS}"}
            - {name: VM, value: "${SANDBOX}"}
            - {name: MODEL, value: "${MODEL}"}
            - {name: SUBJECT, value: "${SUBJECT}"}
            - {name: LITELLM_URL, value: "${LITELLM_URL}"}
            - {name: SANDBOX_IMAGE, value: "${SANDBOX_IMAGE}"}
          command: [/usr/bin/bash, -ec]
          args:
            - |
              g() { virtctl -n "\$NS" ssh "cloud-user@vm/\${VM}" \
                      --identity-file=/ssh-key/key \
                      --local-ssh-opts=-oStrictHostKeyChecking=no \
                      --local-ssh-opts=-oUserKnownHostsFile=/dev/null \
                      --command="export PATH=/usr/local/bin:\\\$HOME/.local/bin:\\\$PATH; \$1" 2>/dev/null; }

              echo "=== 1/3 export the gateway's mTLS client identity ==="
              # The VM generates its own CA, so the client certificate has to come
              # from the VM. The evaluate task mounts this Secret.
              out=/tmp/mtls; mkdir -p "\$out"; chmod 700 "\$out"
              g "cat ~/.local/state/openshell/tls/ca.crt"         > "\$out/ca.crt"
              g "cat ~/.local/state/openshell/tls/client/tls.crt" > "\$out/tls.crt"
              g "cat ~/.local/state/openshell/tls/client/tls.key" > "\$out/tls.key"
              for f in ca.crt tls.crt tls.key; do
                test -s "\$out/\$f" || { echo "ERROR: \$f came back empty"; exit 1; }
                echo "  \$f: \$(wc -c < "\$out/\$f") bytes"
              done
              grep -q "BEGIN CERTIFICATE" "\$out/ca.crt" || { echo "ERROR: ca.crt is not a certificate"; exit 1; }
              kubectl create secret generic openshell-mtls -n "\$NS" \
                --from-file=ca.crt="\$out/ca.crt" \
                --from-file=tls.crt="\$out/tls.crt" \
                --from-file=tls.key="\$out/tls.key" \
                --dry-run=client -o yaml | kubectl apply -f -

              echo "=== 2/3 workspace membership ==="
              if [ -n "\${SUBJECT}" ]; then
                g "openshell workspace member add --workspace 'default' --subject '\${SUBJECT}' --role user 2>&1 | head -5"
                g "openshell workspace member list --workspace default 2>&1 | head -5"
              else
                echo "  SUBJECT not set - skipping. Add it once you know the OIDC subject:"
                echo "    openshell workspace member add --workspace default --subject <sub> --role user"
              fi

              echo "=== 3/3 provider and inference route ==="
              B64="\$(base64 -w0 /profile/profile.yaml)"
              g "echo \$B64 | base64 -d > /tmp/profile.yaml; openshell provider profile import --file /tmp/profile.yaml 2>&1 | head -5"
              # Cluster inference accepts builtin provider types only, so the
              # provider is created as type openai while the imported profile
              # supplies the real endpoint.
              g "openshell provider delete litellm >/dev/null 2>&1; openshell provider create --name litellm --type openai --credential OPENAI_API_KEY=unused --config base_url=\${LITELLM_URL} 2>&1 | head -5"
              g "openshell inference set --provider litellm --model '\${MODEL}' --no-verify 2>&1 | head -5"
              g "openshell inference set --system --provider litellm --model '\${MODEL}' --no-verify 2>&1 | head -5"
              g "openshell inference get 2>&1 | head -10"
              echo "=== 4/4 cache the sandbox image in the VM ==="
              if [ -n "\${SANDBOX_IMAGE}" ] && [ -f /registry-auth/auth.json ]; then
                virtctl -n "\$NS" scp /registry-auth/auth.json "cloud-user@vm/\${VM}:/home/cloud-user/.config/containers/auth.json" \
                  --identity-file=/ssh-key/key \
                  --local-ssh-opts=-oStrictHostKeyChecking=no \
                  --local-ssh-opts=-oUserKnownHostsFile=/dev/null >/dev/null 2>&1 || true
                g "install -d -m 700 ~/.config/containers; chmod 600 ~/.config/containers/auth.json"
                g "podman pull --quiet '\${SANDBOX_IMAGE}' >/dev/null && echo '  cached \${SANDBOX_IMAGE}' || echo '  WARNING: could not pre-pull \${SANDBOX_IMAGE}'"
                # same hygiene as the chart: do not leave the credential behind
                g "rm -f ~/.config/containers/auth.json"
              else
                echo "  SANDBOX_IMAGE not set (or no registry secret) - skipping."
                echo "  A private sandbox image must be cached here, or sandbox create fails later."
              fi

              echo "=== done ==="
JOBYAML

echo "waiting for job/${JOB} ..."
until oc get job "$JOB" -n "$NS" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null | grep -qE "Complete|Failed"; do sleep 10; done
oc logs -n "$NS" "job/$JOB" | grep -v "known hosts"
oc get job "$JOB" -n "$NS" -o jsonpath='{.status.conditions[*].type}{"\n"}'
