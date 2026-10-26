QUAY_NS ?= quay.io/ecosystem-appeng
VERSION ?= 0.1
TASKS_DIR := ../tasks/konflux
PIPELINES_DIR := ../pipelines

TASKS = parse-snapshot prepare test red-team evaluate analyze-scorecard store emit-result \
        create-namespace deploy-mcp wait-ready teardown-namespace \
        mcp-static-eval mcp-conformance-eval mcp-behavioral-eval mcp-compass-publish check-label

MCP_EVAL_TASKS = mcp-static-eval mcp-conformance-eval mcp-behavioral-eval mcp-compass-publish
MCP_EVAL_PIPELINE = mcp-eval-pipeline

.PHONY: bundles mcp-bundles login list clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

bundles: $(addprefix bundle-,$(TASKS)) ## Build and push all Tekton Bundles

mcp-bundles: bundle-mcp-eval ## Build and push MCP eval bundle (tasks + pipeline)

bundle-%: ## Push a single task bundle (e.g. make bundle-parse-snapshot)
	@echo "=== Pushing abevalflow-task-$* ==="
	tkn bundle push $(QUAY_NS)/abevalflow-task-$*:$(VERSION) \
	  -f $(TASKS_DIR)/$*.yaml
	@echo ""

bundle-mcp-eval: ## Push MCP eval bundle (4 tasks + pipeline into single image)
	@echo "=== Pushing abevalflow-mcp-eval-bundle ==="
	tkn bundle push $(QUAY_NS)/abevalflow-mcp-eval-bundle:$(VERSION) \
	  -f $(TASKS_DIR)/mcp-static-eval.yaml \
	  -f $(TASKS_DIR)/mcp-conformance-eval.yaml \
	  -f $(TASKS_DIR)/mcp-behavioral-eval.yaml \
	  -f $(TASKS_DIR)/mcp-compass-publish.yaml \
	  -f $(PIPELINES_DIR)/mcp-eval-pipeline.yaml
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
	@echo "=== MCP Eval Bundle ==="
	@DIGEST=$$(skopeo inspect --format '{{.Digest}}' docker://$(QUAY_NS)/abevalflow-mcp-eval-bundle:$(VERSION) 2>/dev/null || echo "NOT_FOUND"); \
	echo "$(QUAY_NS)/abevalflow-mcp-eval-bundle:$(VERSION)@$$DIGEST"

clean: ## Remove local tkn bundle cache
	rm -rf ~/.cache/tekton/bundles/

login: ## Login to Quay.io
	podman login quay.io
