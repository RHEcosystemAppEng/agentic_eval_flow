"""Auto-imported by every Python process in this image (see ``site`` module).

Judge calls in ``agent_eval.harbor.reward`` use the ``anthropic``/``httpx``
Python clients to reach the LiteLLM proxy over the internal OpenShift route,
which serves the cluster's self-signed ingress certificate. Claude Code (a
Node.js process) already skips verification via ``NODE_TLS_REJECT_UNAUTHORIZED``
(set alongside this in ``OpenShiftEnvironment._pod_manifest``), but Python's
``httpx``/``requests`` do not honor that variable or ``PYTHONHTTPSVERIFY``.

Without this, judge HTTP calls raise an SSL verification error that
``agent_eval.harbor.reward._log_judge_error`` silently swallows (it only
prints ``ScoreRangeError``), surfacing as a confusing, unexplained
"(no judges scored)" with reward 0.0 and no visible traceback.

``reward.py``'s first import is ``agent_eval._bootstrap``, whose
``_inject_os_trust()`` calls ``truststore.inject_into_ssl()`` unless a
CA-bundle env var is already set — replacing ``ssl.SSLContext`` globally with
one backed by the OS trust store *after* this module has already run. That
overrides our own ``verify=False`` forcing below, since the transport's
"insecure" context construction goes through the now-patched
``ssl.SSLContext``. We neutralize ``truststore.inject_into_ssl`` itself
(rather than only patching httpx/requests) so import order can't matter.

Opt-in via ``AEH_INSECURE_SSL=1`` so this never silently weakens verification
outside the trial-pod judge-call context it exists for.
"""

import os


def _disable_ssl_verification() -> None:
    import ssl

    try:
        ssl._create_default_https_context = ssl._create_unverified_context
    except Exception:
        pass

    # litellm has its own SSL_VERIFY env var, read fresh on every request via
    # get_ssl_verify() (litellm/llms/custom_httpx/http_handler.py), and its
    # resolved value is passed explicitly as aiohttp's per-request `ssl=`
    # kwarg -- which overrides whatever the TCPConnector was constructed
    # with. Without this, our TCPConnector patch below is silently ignored
    # for every litellm-routed call (e.g. cisco-ai-skill-scanner --use-llm).
    os.environ.setdefault("SSL_VERIFY", "False")

    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    def _make_patched_init(orig):
        def _patched_init(self, *args, **kwargs):
            kwargs["verify"] = False
            return orig(self, *args, **kwargs)
        return _patched_init

    # Patch both httpx and httpx2 (the anthropic SDK vendors/depends on a
    # renamed "httpx2" package as its HTTP client — a plain httpx.Client
    # instance is rejected with a TypeError, so both must be patched for the
    # anthropic client used by the LLM judge to actually skip verification).
    #
    # Client/AsyncClient AND HTTPTransport/AsyncHTTPTransport both need
    # patching: anthropic._base_client pre-builds its own
    # HTTPTransport(**transport_kwargs) from the *original* (unpatched)
    # kwargs whenever the caller doesn't pass transport= explicitly, then
    # passes that transport into Client(...). Once transport= is set, httpx
    # ignores verify= entirely — so patching Client.__init__ alone silently
    # does nothing for the anthropic SDK's actual request path.
    for module_name in ("httpx", "httpx2"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue

        for cls_name in ("Client", "AsyncClient", "HTTPTransport", "AsyncHTTPTransport"):
            cls = getattr(module, cls_name, None)
            if cls is None:
                continue
            try:
                cls.__init__ = _make_patched_init(cls.__init__)
            except Exception:
                pass

    try:
        import requests

        _orig_request = requests.Session.request

        def _patched_request(self, *args, **kwargs):
            kwargs["verify"] = False
            return _orig_request(self, *args, **kwargs)

        requests.Session.request = _patched_request
    except Exception:
        pass

    # litellm's async path (used by cisco-ai-skill-scanner's --use-llm, and
    # potentially others) doesn't go through httpx's own transport at all —
    # litellm.llms.custom_httpx.aiohttp_transport implements a custom
    # httpx.AsyncBaseTransport backed by a real aiohttp.ClientSession/
    # TCPConnector, so none of the httpx/httpx2 patches above ever run for
    # it. Patch aiohttp's connector directly too.
    try:
        import aiohttp

        _orig_connector_init = aiohttp.TCPConnector.__init__

        def _patched_connector_init(self, *args, **kwargs):
            kwargs["ssl"] = False
            return _orig_connector_init(self, *args, **kwargs)

        aiohttp.TCPConnector.__init__ = _patched_connector_init
    except Exception:
        pass

    # Neutralize agent_eval._bootstrap's truststore.inject_into_ssl() before it
    # can run: it replaces ssl.SSLContext globally with an OS-trust-store-backed
    # one, which httpx2's "insecure" HTTPTransport construction goes through —
    # silently undoing the verify=False forcing above regardless of import order.
    try:
        import truststore

        truststore.inject_into_ssl = lambda *args, **kwargs: None
    except ImportError:
        pass


if os.environ.get("AEH_INSECURE_SSL", "").strip().lower() in ("1", "true", "yes"):
    _disable_ssl_verification()
