CONTAINER_ENGINE ?= podman
IMAGE_REPOSITORY ?= quay.io/ecosystem-appeng/agent-eval-harness
VERSION ?= v3.0
PLATFORM ?= linux/amd64
IMAGES := agent-eval-harness aeh-task aeh-trial security-scan skillmd-scan
PURPOSE_agent-eval-harness := runner
PURPOSE_aeh-task := task
PURPOSE_aeh-trial := trial
PURPOSE_security-scan := security-scan
PURPOSE_skillmd-scan := skillmd-scan

.PHONY: images test $(IMAGES)
images: $(IMAGES)
$(IMAGES):
	$(CONTAINER_ENGINE) build --platform $(PLATFORM) -f containers/$@/Containerfile -t $(IMAGE_REPOSITORY):$(VERSION)-$(PURPOSE_$@) .

test:
	uv run --locked --extra dev --extra observability pytest tests/ -q \
		--cov=abevalflow --cov=scripts --cov-report=term-missing --cov-fail-under=80
	uv run --locked --extra dev --extra observability ruff check abevalflow scripts tests containers
	uv run --locked --extra dev --extra observability ruff format --check abevalflow scripts tests containers
