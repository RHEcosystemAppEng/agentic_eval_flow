"""Standalone, scenario-driven MCP server for Phase 1 tenant VM evaluations.

The server implements only the MCP surface needed by the four VM skills. It
does not import or run OSAC production code.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
from typing_extensions import NotRequired

from mcp.server import MCPServer
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from evals.tenant_vm.mock_server.fixtures import Fixture, build_fixture


ListResourceType = Literal[
    "compute_instance",
    "compute_instance_catalog_item",
    "compute_instance_template",
    "disk_image",
    "instance_type",
    "project",
    "secret_reference",
    "security_group",
    "storage_tier",
    "subnet",
    "virtual_network",
]
LIST_RESOURCE_TYPES = frozenset(ListResourceType.__args__)
TOOL_NAMES = frozenset(
    {
        "check_connection",
        "list_resources",
        "get_resource",
        "create_compute_instance",
        "delete_compute_instance",
    }
)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
GetResourceType = Literal[
    "compute_instance",
    "compute_instance_catalog_item",
    "compute_instance_template",
    "disk_image",
    "instance_type",
    "project",
    "security_group",
    "storage_tier",
    "subnet",
    "virtual_network",
]
GET_RESOURCE_TYPES = frozenset(GetResourceType.__args__)


class ResourceSummary(TypedDict):
    id: str
    version: int
    name: NotRequired[str]
    state: NotRequired[str]
    type: NotRequired[str]


class ListResourcesOutput(TypedDict):
    offset: int
    size: int
    total: int
    items: list[ResourceSummary]


class GetResourceOutput(TypedDict):
    resource: dict[str, Any]


class ConnectionCaller(TypedDict):
    subject: str
    tenants: list[str]
    tenants_truncated: bool


class ConnectionServer(TypedDict):
    name: str
    version: str


class ConnectionPermission(TypedDict):
    tenant: str
    service: str
    method: str
    allowed: bool


class CheckConnectionOutput(TypedDict):
    endpoint: str
    server: ConnectionServer
    caller: ConnectionCaller
    resource_families: list[str]
    tools: list[str]
    permission_summary: list[ConnectionPermission]
    permission_summary_truncated: bool


class BootDiskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_gib: int | None = Field(
        default=None,
        description="optional boot disk size in GiB; must be greater than zero",
    )
    storage_tier_id: str = Field(
        default="",
        description="optional ID from list_resources(storage_tier)",
    )


class NetworkAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subnet_id: str = Field(
        default="",
        description="optional ID from list_resources(subnet); omit to use the tenant default Subnet",
    )
    security_group_ids: list[str] | None = Field(
        default=None,
        description="optional IDs from list_resources(security_group)",
    )


class CreateComputeInstanceOutput(TypedDict):
    id: str
    version: int
    state: str


class DeleteComputeInstanceOutput(TypedDict):
    id: str


SUPPORTED_RESOURCE_FAMILIES = [
    "compute_instance_catalog_item",
    "compute_instance_template",
    "instance_type",
    "disk_image",
    "storage_tier",
    "project",
    "virtual_network",
    "subnet",
    "security_group",
    "compute_instance",
    "secret_reference",
]

READ_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
CREATE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
DELETE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


class Audit:
    """Write sanitized tool-call traces outside the agent workspace."""

    def __init__(
        self, fixture: str, path: Path | None, fixture_data: Fixture | None = None
    ) -> None:
        self.fixture = fixture
        self.path = path
        self.fixture_data = fixture_data
        self.known_ids = {
            str(item["id"])
            for resources in (fixture_data.resources.values() if fixture_data else [])
            for item in resources
        }
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        if self.path is not None:
            with self.lock:
                self._write_locked()

    def begin(
        self,
        tool_value: Any,
        arguments_value: Any,
    ) -> dict[str, Any]:
        tool = (
            tool_value
            if isinstance(tool_value, str) and tool_value in TOOL_NAMES
            else "<unknown>"
        )
        event = {
            "sequence": 0,
            "tool": tool,
            "arguments": self._sanitize_arguments(tool, arguments_value),
            "outcome": "pending",
        }
        with self.lock:
            event["sequence"] = len(self.events) + 1
            self.events.append(event)
            self._write_locked()
        return event

    def finish(self, event: dict[str, Any], outcome: str) -> None:
        with self.lock:
            event["outcome"] = outcome
            self._write_locked()

    def register_identifier(self, resource_id: str) -> None:
        with self.lock:
            self.known_ids.add(resource_id)

    def _safe_identifier(self, value: Any) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if normalized and IDENTIFIER.fullmatch(normalized):
            with self.lock:
                if normalized in self.known_ids:
                    return normalized
        return "<redacted>"

    def _sanitize_arguments(self, tool: str, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            return {"arguments_redacted": True}

        allowed: dict[str, set[str]] = {
            "check_connection": set(),
            "list_resources": {"resource_type", "filter", "offset", "page_size"},
            "get_resource": {"resource_type", "id"},
            "create_compute_instance": {
                "name",
                "catalog_item",
                "disk_image_id",
                "project_id",
                "instance_type_id",
                "boot_disk",
                "network_attachments",
            },
            "delete_compute_instance": {"id"},
            "<unknown>": set(),
        }
        safe: dict[str, Any] = {}
        unknown_count = 0
        for key, value in arguments.items():
            if not isinstance(key, str) or key not in allowed[tool]:
                unknown_count += 1
                continue
            if key in {"name", "filter"}:
                safe[key] = "<redacted>"
            elif key == "resource_type":
                resource_types = (
                    LIST_RESOURCE_TYPES
                    if tool == "list_resources"
                    else GET_RESOURCE_TYPES
                )
                safe[key] = (
                    value
                    if isinstance(value, str) and value in resource_types
                    else "<invalid>"
                )
            elif key in {
                "id",
                "catalog_item",
                "disk_image_id",
                "project_id",
                "instance_type_id",
            }:
                safe[key] = self._safe_identifier(value)
            elif key in {"offset", "page_size"}:
                safe[key] = (
                    value
                    if isinstance(value, int) and not isinstance(value, bool)
                    else "<invalid>"
                )
            elif key == "boot_disk":
                safe["boot_disk"] = self._sanitize_boot_disk(value)
            elif key == "network_attachments":
                safe["network_attachments"] = self._sanitize_network_attachments(value)
        if unknown_count:
            safe["unknown_argument_count"] = unknown_count
        return safe

    def _sanitize_boot_disk(self, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return "<redacted>"
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key == "size_gib":
                result[key] = (
                    item
                    if isinstance(item, int) and not isinstance(item, bool)
                    else "<invalid>"
                )
            elif key == "storage_tier_id":
                result[key] = self._safe_identifier(item)
        return result

    def _sanitize_network_attachments(self, value: Any) -> Any:
        if not isinstance(value, list):
            return "<redacted>"
        result: list[dict[str, Any]] = []
        for attachment in value:
            if not isinstance(attachment, Mapping):
                result.append({"attachment_redacted": True})
                continue
            safe_attachment: dict[str, Any] = {}
            if "subnet_id" in attachment:
                safe_attachment["subnet_id"] = self._safe_identifier(
                    attachment["subnet_id"]
                )
            if "security_group_ids" in attachment:
                group_ids = attachment["security_group_ids"]
                safe_attachment["security_group_ids"] = (
                    [self._safe_identifier(group_id) for group_id in group_ids]
                    if isinstance(group_ids, list)
                    else "<redacted>"
                )
            result.append(safe_attachment)
        return result

    def _write_locked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary_path.write_text(
            json.dumps({"fixture": self.fixture, "events": self.events}, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    async def middleware(
        self, context: ServerRequestContext[Any, Any], call_next: Any
    ) -> Any:
        """Capture tools/call before SDK validation, lookup, and dispatch."""
        if context.method != "tools/call":
            return await call_next(context)

        params = context.params if isinstance(context.params, Mapping) else {}
        event = self.begin(params.get("name"), params.get("arguments"))
        unknown_count = event["arguments"].get("unknown_argument_count", 0)
        if event["tool"] != "<unknown>" and unknown_count:
            self.finish(event, "error")
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=str(
                            _invalid_request("arguments contain unsupported fields")
                        ),
                    )
                ],
                is_error=True,
            )

        try:
            result = await call_next(context)
        except BaseException:
            self.finish(event, "error")
            raise
        is_error = getattr(result, "is_error", False)
        if isinstance(result, Mapping):
            is_error = result.get("isError", result.get("is_error", is_error))
        if is_error and isinstance(result, dict):
            content = result.get("content", [])
            text = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, Mapping)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            )
            prefix = f"Error executing tool {params.get('name')}"
            if text.startswith(prefix):
                payload = text[len(prefix) :].removeprefix(": ")
                try:
                    json.loads(payload)
                except json.JSONDecodeError:
                    payload = str(
                        _invalid_request("arguments do not match the tool input schema")
                    )
                result["content"] = [{"type": "text", "text": payload}]
        self.finish(event, "error" if is_error else "ok")
        return result


def _tool_error(
    category: str,
    grpc_code: str,
    message: str,
    retryable: bool = False,
    details: list[str] | None = None,
) -> ToolError:
    return ToolError(
        json.dumps(
            {
                "request_id": str(uuid.uuid4()),
                "category": category,
                "grpc_code": grpc_code,
                "message": message,
                "retryable": retryable,
                "details": details or [],
            },
            separators=(",", ":"),
        )
    )


def _invalid_request(detail: str) -> ToolError:
    return _tool_error(
        "invalid_request",
        "InvalidArgument",
        "The request arguments were rejected. Correct the request and try again.",
        details=[detail],
    )


def _public_api_invalid_argument() -> ToolError:
    return _tool_error(
        "invalid_request",
        "InvalidArgument",
        "The request arguments were rejected. Correct the request and try again.",
    )


def _not_found() -> ToolError:
    return _tool_error(
        "not_found",
        "NotFound",
        "The requested OSAC resource was not found.",
    )


def _missing_prerequisite(kind: str, resource_id: str) -> ToolError:
    return _tool_error(
        "not_found",
        "NotFound",
        "A required prerequisite is missing. The dependent operation was not performed.",
        details=[f"{kind} {resource_id} is missing"],
    )


class Runtime:
    def __init__(self, fixture: Fixture, audit: Audit) -> None:
        self.fixture = fixture
        self.audit = audit
        self.created_count = 0

    @staticmethod
    def _summary(item: dict[str, Any]) -> ResourceSummary:
        summary: ResourceSummary = {
            "id": str(item["id"]),
            "version": int(item.get("version", 1)),
        }
        for key in ("name", "state", "type"):
            value = item.get(key)
            if value is not None:
                summary[key] = str(value)
        return summary

    def _advance_deletion(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        visible: list[dict[str, Any]] = []
        for item in list(items):
            resource_id = str(item["id"])
            if resource_id not in self.fixture.deleting_reads:
                visible.append(item)
                continue
            if self.fixture.deleting_reads[resource_id] == 0:
                self.fixture.deleting_reads[resource_id] = 1
                item["state"] = "COMPUTE_INSTANCE_STATE_DELETING"
                self.fixture.details[resource_id]["state"] = (
                    "COMPUTE_INSTANCE_STATE_DELETING"
                )
                visible.append(item)
            else:
                items.remove(item)
                self.fixture.details.pop(resource_id, None)
                self.fixture.deleting_reads.pop(resource_id, None)
        return visible

    @staticmethod
    def _apply_filter(
        items: list[dict[str, Any]], expression: str
    ) -> list[dict[str, Any]]:
        if not expression:
            return items
        quoted = r'("(?:[^"\\]|\\.)*")'
        patterns = [
            (rf"this\.id\s*==\s*{quoted}", "id", "equals"),
            (rf"this\.metadata\.name\s*==\s*{quoted}", "name", "equals"),
            (rf"this\.metadata\.name\.startsWith\({quoted}\)", "name", "starts"),
            (rf"this\.status\.state\s*==\s*{quoted}", "state", "equals"),
        ]
        for pattern, field_name, operation in patterns:
            match = re.fullmatch(pattern, expression)
            if not match:
                continue
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise _invalid_request(
                    "filter must contain a valid CEL string literal"
                ) from exc
            if operation == "starts":
                return [
                    item
                    for item in items
                    if str(item.get(field_name, "")).startswith(value)
                ]
            return [item for item in items if item.get(field_name) == value]
        if expression == "this.type == SECRET_TYPE_PULL_SECRET":
            return [item for item in items if item.get("type") == "pull_secret"]
        raise _invalid_request(
            "the mock supports only its documented CEL filter subset"
        )

    def list_resources(
        self,
        resource_type: ListResourceType,
        filter: str,
        offset: int,
        page_size: int,
    ) -> ListResourcesOutput:
        if offset < 0 or page_size < 0 or page_size > 100:
            raise _invalid_request(
                "offset and page_size are outside the supported range"
            )
        all_items = self.fixture.resources.get(resource_type, [])
        all_items = self._advance_deletion(all_items)
        filtered = self._apply_filter(all_items, filter)
        actual_page_size = 50 if page_size == 0 else page_size
        total = len(filtered)
        page = filtered[offset : offset + actual_page_size]
        return {
            "offset": offset,
            "size": len(page),
            "total": total,
            "items": [self._summary(item) for item in page],
        }

    def _find_resource(
        self, resource_type: str, resource_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.fixture.resources.get(resource_type, [])
                if item.get("id") == resource_id
            ),
            None,
        )

    def _require_reference(
        self, resource_type: str, resource_id: str | None, kind: str
    ) -> None:
        if resource_id and self._find_resource(resource_type, resource_id) is None:
            raise _missing_prerequisite(kind, resource_id)

    def get_resource(
        self, resource_type: GetResourceType, resource_id: str
    ) -> GetResourceOutput:
        if not resource_id.strip():
            raise _invalid_request("id must not be empty")
        items = self.fixture.resources.get(resource_type, [])
        self._advance_deletion(items)
        item = self._find_resource(resource_type, resource_id)
        if item is None:
            raise _not_found()
        resource = self.fixture.details.get(resource_id, item)
        return {"resource": dict(resource)}

    def create_compute_instance(
        self,
        name: str,
        catalog_item: str,
        disk_image_id: str | None,
        project_id: str | None,
        instance_type_id: str | None,
        boot_disk: BootDiskInput | None,
        network_attachments: list[NetworkAttachmentInput] | None,
    ) -> CreateComputeInstanceOutput:
        name = name.strip()
        catalog_item = catalog_item.strip()
        disk_image_id = disk_image_id.strip() if disk_image_id else None
        project_id = project_id.strip() if project_id else None
        instance_type_id = instance_type_id.strip() if instance_type_id else None
        if not name or not catalog_item:
            raise _invalid_request("name and catalog_item are required")

        catalog = self._find_resource("compute_instance_catalog_item", catalog_item)
        if catalog is None:
            raise _missing_prerequisite("catalog item", catalog_item)
        if catalog.get("state") != "published":
            raise _invalid_request(f"catalog item {catalog_item} is not published")

        if self.fixture.require_explicit_project and not project_id:
            raise _invalid_request("an explicit Project is required for this fixture")
        if project_id:
            project = self._find_resource("project", project_id)
            if project is None:
                raise _missing_prerequisite("Project", project_id)
            if project.get("state") != "PROJECT_STATE_ACTIVE":
                raise _invalid_request(f"project {project_id} is not active")

        overrides = {
            "disk_image_id": bool(disk_image_id),
            "instance_type_id": bool(instance_type_id),
            "boot_disk": boot_disk is not None,
            "network_attachments": bool(network_attachments),
        }
        permitted = self.fixture.catalog_overrides.get(catalog_item, set())
        if any(
            supplied and field_name not in permitted
            for field_name, supplied in overrides.items()
        ):
            raise _public_api_invalid_argument()

        self._require_reference("disk_image", disk_image_id, "disk image")
        self._require_reference("instance_type", instance_type_id, "instance type")
        if boot_disk is not None:
            storage_tier_id = boot_disk.storage_tier_id.strip()
            if boot_disk.size_gib is not None and boot_disk.size_gib <= 0:
                raise _invalid_request("boot_disk.size_gib must be greater than zero")
            if boot_disk.size_gib is None and not storage_tier_id:
                raise _invalid_request(
                    "boot_disk must include size_gib and/or storage_tier_id"
                )
            self._require_reference("storage_tier", storage_tier_id, "storage tier")

        if network_attachments and len(network_attachments) > 1:
            raise _invalid_request(
                "network_attachments must contain at most one attachment"
            )
        group_ids: list[str] = []
        for attachment in network_attachments or []:
            subnet_id = attachment.subnet_id.strip() if attachment.subnet_id else ""
            self._require_reference("subnet", subnet_id, "Subnet")
            for group_id in attachment.security_group_ids or []:
                normalized_id = group_id.strip()
                if not normalized_id:
                    raise _invalid_request(
                        "network_attachments[0].security_group_ids entries are required"
                    )
                group_ids.append(normalized_id)
        unique_group_ids = set(group_ids)
        if len(unique_group_ids) > 100:
            raise _invalid_request(
                "network_attachments must reference at most 100 unique security groups"
            )
        for group_id in unique_group_ids:
            self._require_reference("security_group", group_id, "SecurityGroup")

        if any(
            item.get("name") == name
            for item in self.fixture.resources.get("compute_instance", [])
        ):
            raise _tool_error(
                "conflict",
                "AlreadyExists",
                "The resource already exists or conflicts with current state. Resolve the conflict and retry.",
            )

        self.created_count += 1
        resource_id = (
            "vm-uncertain-created-001"
            if self.fixture.create_mode == "uncertain"
            else f"vm-created-{self.created_count:03d}"
        )
        item = self.fixture.add(
            "compute_instance",
            resource_id,
            name,
            state="COMPUTE_INSTANCE_STATE_STARTING",
        )
        self.audit.register_identifier(resource_id)
        if self.fixture.create_mode == "uncertain":
            raise _tool_error(
                "service_unavailable",
                "DeadlineExceeded",
                "The OSAC request timed out. Retry after checking service status.",
                retryable=True,
            )
        return {
            "id": str(item["id"]),
            "version": int(item["version"]),
            "state": str(item["state"]),
        }

    def delete_compute_instance(self, resource_id: str) -> DeleteComputeInstanceOutput:
        resource_id = resource_id.strip()
        if not resource_id:
            raise _invalid_request("id is required")
        if self.fixture.delete_mode == "unsupported":
            raise _tool_error(
                "protocol_incompatible",
                "FailedPrecondition",
                "The request is incompatible with the current OSAC service. Update the client and retry.",
            )
        if self.fixture.delete_mode == "denied":
            raise _tool_error(
                "authorization",
                "PermissionDenied",
                "The caller is not authorized for this operation. Request the needed access.",
            )
        item = self._find_resource("compute_instance", resource_id)
        if item is None:
            raise _not_found()
        item["state"] = "COMPUTE_INSTANCE_STATE_DELETING"
        self.fixture.details[resource_id]["state"] = "COMPUTE_INSTANCE_STATE_DELETING"
        self.fixture.deleting_reads[resource_id] = 0
        return {"id": resource_id}


def create_server(fixture: Fixture, audit: Audit, endpoint: str) -> MCPServer:
    server = MCPServer(
        name="osac-deployment-mcp",
        version="mock-1.0.0",
        instructions=(
            "OSAC tenant deployment MCP test fixture. Responses are deterministic, "
            "scenario-specific test data."
        ),
    )
    server.middleware.append(audit.middleware)
    runtime = Runtime(fixture, audit)
    list_types_description = ", ".join(sorted(LIST_RESOURCE_TYPES))
    get_types_description = ", ".join(sorted(GET_RESOURCE_TYPES))

    def check_connection() -> CheckConnectionOutput:
        tools = [
            "check_connection",
            "list_resources",
            "get_resource",
            "create_compute_instance",
        ]
        if fixture.delete_tool_available:
            tools.append("delete_compute_instance")
        tools.sort()
        return {
            "endpoint": endpoint,
            "server": {"name": "osac-deployment-mcp", "version": "mock-1.0.0"},
            "caller": {
                "subject": "tenant-user-1",
                "tenants": ["tenant-blue"],
                "tenants_truncated": False,
            },
            "resource_families": sorted(SUPPORTED_RESOURCE_FAMILIES),
            "tools": tools,
            "permission_summary": [
                {
                    "tenant": "tenant-blue",
                    "service": "osac.public.v1.ComputeInstances",
                    "method": "List",
                    "allowed": True,
                },
                {
                    "tenant": "tenant-blue",
                    "service": "osac.public.v1.ComputeInstances",
                    "method": "Create",
                    "allowed": True,
                },
                {
                    "tenant": "tenant-blue",
                    "service": "osac.public.v1.ComputeInstances",
                    "method": "Update",
                    "allowed": True,
                },
            ],
            "permission_summary_truncated": False,
        }

    server.add_tool(
        check_connection,
        name="check_connection",
        title="Check OSAC MCP connection",
        description=(
            "Use this tool to report the signed-in caller's identity and ComputeInstances List, Create, "
            "and Update permissions. It does not verify Get or Delete permissions."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )

    def list_resources(
        resource_type: Annotated[
            ListResourceType,
            Field(description="supported OSAC deployment resource type"),
        ],
        filter: Annotated[
            str, Field(description="optional CEL filter expression to narrow results")
        ] = "",
        offset: Annotated[int, Field(ge=0, description="zero-based result offset")] = 0,
        page_size: Annotated[
            int,
            Field(ge=0, le=100, description="maximum result count from 1 through 100"),
        ] = 0,
    ) -> ListResourcesOutput:
        return runtime.list_resources(resource_type, filter, offset, page_size)

    server.add_tool(
        list_resources,
        name="list_resources",
        title="List OSAC deployment resources",
        description=(
            "Use this tool to discover or compare tenant-visible OSAC deployment options and existing compute "
            "instances. resource_type must be one of: "
            f"{list_types_description}."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )

    def get_resource(
        resource_type: Annotated[
            GetResourceType,
            Field(description="supported OSAC deployment resource type"),
        ],
        id: Annotated[str, Field(description="resource ID")],
    ) -> GetResourceOutput:
        if resource_type == "secret_reference":
            raise ToolError("get_resource does not support secret_reference.")
        return runtime.get_resource(resource_type, id)

    server.add_tool(
        get_resource,
        name="get_resource",
        title="Get OSAC deployment resource",
        description=(
            "Use this tool to inspect one selected resource, verify catalog or reference details, or report current "
            f"compute instance state. resource_type must be one of: {get_types_description}. "
            "Only resource types with Get support are accepted by this tool."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )

    def create_compute_instance(
        name: Annotated[str, Field(description="name for the new compute instance")],
        catalog_item: Annotated[
            str, Field(description="ID of the compute instance catalog item")
        ],
        disk_image_id: Annotated[
            str,
            Field(
                description="optional ID from list_resources(disk_image); omit to use catalog or template defaults; the catalog item must permit the override"
            ),
        ] = "",
        project_id: Annotated[
            str,
            Field(
                description="optional ID from list_resources(project); omit to use the caller's default Project"
            ),
        ] = "",
        instance_type_id: Annotated[
            str,
            Field(
                description="optional ID from list_resources(instance_type); the catalog item must permit the override"
            ),
        ] = "",
        boot_disk: Annotated[
            BootDiskInput | None,
            Field(
                description='optional object with size_gib and/or storage_tier_id; example: {"size_gib":20,"storage_tier_id":"<storage-tier-id>"}; copy the ID from list_resources(storage_tier), not its name or a nested storageTier object; omit to use catalog defaults; the catalog item must permit each supplied field'
            ),
        ] = None,
        network_attachments: Annotated[
            list[NetworkAttachmentInput] | None,
            Field(
                description='optional array of objects, maximum one attachment; example: [{"subnet_id":"subnet-id","security_group_ids":["security-group-id"]}]; omit to use the catalog or tenant default network'
            ),
        ] = None,
    ) -> CreateComputeInstanceOutput:
        return runtime.create_compute_instance(
            name,
            catalog_item,
            disk_image_id,
            project_id,
            instance_type_id,
            boot_disk,
            network_attachments,
        )

    server.add_tool(
        create_compute_instance,
        name="create_compute_instance",
        title="Create OSAC compute instance",
        description=(
            "Use this tool only after the user confirms the requested VM and you have inspected the catalog item and any selected "
            "override resources. Use this tool's snake_case fields and resource IDs, not get_resource's camelCase reference objects. "
            "Select disk_image_id from list_resources(disk_image) when the catalog or template does not supply an image, and "
            "project_id from list_resources(project) when the VM should not use the caller's default Project. "
            "For a disk override, boot_disk uses size_gib and/or storage_tier_id (a string ID), not a nested storageTier object. "
            "If overriding networking, network_attachments must contain maximum one object with subnet_id and "
            "optional security_group_ids, not an array of subnet ID strings. Creates a compute instance asynchronously; "
            "acceptance does not mean the VM is ready."
        ),
        annotations=CREATE_ANNOTATIONS,
        structured_output=True,
    )

    if fixture.delete_tool_available:

        def delete_compute_instance(
            id: Annotated[str, Field(description="compute instance ID")],
        ) -> DeleteComputeInstanceOutput:
            return runtime.delete_compute_instance(id)

        server.add_tool(
            delete_compute_instance,
            name="delete_compute_instance",
            title="Delete OSAC compute instance",
            description=(
                "Use this tool only when the user explicitly requests deletion or cleanup. Deletes a compute instance by ID "
                "asynchronously."
            ),
            annotations=DELETE_ANNOTATIONS,
            structured_output=True,
        )

    for tool_name in TOOL_NAMES:
        registered_tool = server._tool_manager.get_tool(tool_name)
        if registered_tool is not None:
            registered_tool.parameters["additionalProperties"] = False

    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tenant VM mock MCP server.")
    parser.add_argument(
        "--fixture",
        required=True,
        help="Jira case ID, with an optional colon variant (for example TOOL-DENIED-UNAVAILABLE:denied).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Loopback address to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Local TCP port.")
    parser.add_argument(
        "--audit-file",
        type=Path,
        help="Harness-only call trace path outside the agent workspace.",
    )
    return parser.parse_args()


def endpoint_url(host: str, port: int) -> str:
    authority = f"[{host}]" if ":" in host else host
    return f"http://{authority}:{port}/mcp"


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("The local mock server only binds to loopback addresses.")
    if not 0 < args.port < 65536:
        raise SystemExit("--port must be between 1 and 65535.")
    fixture = build_fixture(args.fixture)
    endpoint = endpoint_url(args.host, args.port)
    audit = Audit(args.fixture, args.audit_file, fixture)
    server = create_server(fixture, audit, endpoint)
    print(
        f"Tenant VM mock MCP server ready at {endpoint} for {args.fixture}", flush=True
    )
    server.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
