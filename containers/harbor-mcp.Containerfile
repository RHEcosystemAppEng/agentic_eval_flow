ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN pip install --no-cache-dir harbor==0.26.0 minio==7.2.20 'psycopg[binary]' sqlalchemy tenacity scipy pytest==8.4.1 tomlkit mcp==2.3.0
COPY abevalflow /opt/ci/abevalflow
COPY scripts /opt/ci/scripts
COPY config/mcp-mocks /opt/ci/config/mcp-mocks
ENV PYTHONPATH=/opt/ci:/opt/agent-eval-harness PYTHONDONTWRITEBYTECODE=1
USER 1001
