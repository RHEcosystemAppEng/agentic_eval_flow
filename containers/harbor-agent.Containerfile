ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN pip install --no-cache-dir pytest==8.4.1 \
    && npm install -g @openai/codex@0.169.0 \
    && dnf install -y ripgrep && dnf clean all \
    && mkdir -p /installed-agent && chgrp 0 /installed-agent && chmod g=u /installed-agent
USER 1001
