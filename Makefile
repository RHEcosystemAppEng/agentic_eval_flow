CONTAINER_ENGINE ?= podman
IMAGE_REPOSITORY ?= quay.io/ecosystem-appeng/agent-eval-harness
VERSION ?= v1.0.5
PLATFORM ?= linux/amd64
IMAGES := agent-eval-harness aeh-trial
PURPOSE_agent-eval-harness := runner
PURPOSE_aeh-trial := trial

.PHONY: images push test $(IMAGES) $(addprefix push-,$(IMAGES))
images: $(IMAGES)
$(IMAGES):
	$(CONTAINER_ENGINE) build --platform $(PLATFORM) -f containers/$@/Containerfile -t $(IMAGE_REPOSITORY):$(VERSION)-$(PURPOSE_$@) .

push: $(addprefix push-,$(IMAGES))
$(addprefix push-,$(IMAGES)): push-%:
	$(CONTAINER_ENGINE) push $(IMAGE_REPOSITORY):$(VERSION)-$(PURPOSE_$*)

test:
	uv run --locked --extra dev --extra observability pytest tests/ -q \
		--cov=abevalflow --cov-report=term-missing --cov-fail-under=80
	uv run --locked --extra dev --extra observability ruff check abevalflow tests containers
	uv run --locked --extra dev --extra observability ruff format --check abevalflow tests containers
