ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN pip install --no-cache-dir pytest==8.4.1 ripgrep==14.1.0 \
    && npm install -g @openai/codex@0.169.0 \
    && mkdir -p /installed-agent && chgrp 0 /installed-agent && chmod g=u /installed-agent
USER 1001
