# Third-party runtime dependencies

Agentic Eval Flow invokes Agent Eval Harness and Harbor as external dependencies.
AEH is installed from its complete, unmodified upstream source distribution.
No AEH implementation is bundled in the application package.

AEH and Harbor are licensed under Apache-2.0. The trial image also installs the
fixture-backed generic MCP mock server from its pinned upstream repository; its
license is retained in that installation. Each runtime dependency retains its own
license and distribution files.
