QUAY_NS ?= quay.io/ecosystem-appeng
VERSION ?= AEH
TASKS_DIR := ../tasks/konflux

TASKS = parse-snapshot prepare test red-team evaluate analyze-scorecard store emit-result

.PHONY: bundles login list clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

bundles: $(addprefix bundle-,$(TASKS)) ## Build and push all Tekton Bundles

bundle-%: ## Push a single task bundle (e.g. make bundle-parse-snapshot)
	@echo "=== Pushing abevalflow-task-$* ==="
	tkn bundle push $(QUAY_NS)/abevalflow-task-$*:$(VERSION) \
	  -f $(TASKS_DIR)/$*.yaml
	@echo ""

list: ## List all bundles in the registry
	@for task in $(TASKS); do \
	  echo "--- abevalflow-task-$$task ---"; \
	  tkn bundle list $(QUAY_NS)/abevalflow-task-$$task 2>/dev/null || echo "  (not found)"; \
	done

digests: ## Print SHA digests for all pushed bundles
	@for task in $(TASKS); do \
	  DIGEST=$$(skopeo inspect --format '{{.Digest}}' docker://$(QUAY_NS)/abevalflow-task-$$task:$(VERSION) 2>/dev/null || echo "NOT_FOUND"); \
	  echo "$(QUAY_NS)/abevalflow-task-$$task:$(VERSION)@$$DIGEST"; \
	done

clean: ## Remove local tkn bundle cache
	rm -rf ~/.cache/tekton/bundles/

login: ## Login to Quay.io
	podman login quay.io
