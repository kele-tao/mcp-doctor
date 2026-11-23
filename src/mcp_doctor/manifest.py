"""Typed views over what a server advertises, plus manifest export.

The manifest is the stable, diffable artefact mcp-doctor writes with
inspect --manifest. It contains no timestamps and no absolute paths, so two runs
against the same server produce byte-identical JSON. That property is what lets
a reviewed manifest become a lock file in CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .client import Peer
from .jsonschema import schema_summary
from .version import __version__

MANIFEST_FORMAT_VERSION = "1"


@dataclass
class Tool:
    """One tool as advertised by tools/list."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    output_schema: dict | None = None
    annotations: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def required(self) -> list[str]:
        """Names the input schema marks as required."""
        value = self.input_schema.get("required")
        return [str(x) for x in value] if isinstance(value, list) else []

    @property
    def properties(self) -> dict:
        """The input schema properties mapping."""
        value = self.input_schema.get("properties")
        return value if isinstance(value, dict) else {}

    def signature(self) -> str:
        """A compact call signature such as add(a: integer, b: integer) -> object."""
        parts = []
        for name, schema in self.properties.items():
            kind = schema.get("type", "any") if isinstance(schema, dict) else "any"
            if isinstance(kind, list):
                kind = "|".join(str(k) for k in kind)
            marker = "" if name in self.required else "?"
            parts.append(f"{name}{marker}: {kind}")
        returns = "object"
        if isinstance(self.output_schema, dict):
            declared = self.output_schema.get("type")
            if isinstance(declared, str):
                returns = declared
        return f"{self.name}({', '.join(parts)}) -> {returns}"

    def complexity(self) -> int:
        """A small integer describing how hard the input schema is to satisfy."""
        summary = schema_summary(self.input_schema)
        return (
            summary["properties"]
            + 2 * summary["required"]
            + 2 * summary["branches"]
            + summary["depth"]
        )


@dataclass
class Resource:
    """One resource as advertised by resources/list."""

    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    size: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ResourceTemplate:
    """One resource template as advertised by resources/templates/list."""

    uri_template: str
    name: str
    description: str = ""
    mime_type: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Prompt:
    """One prompt as advertised by prompts/list."""

    name: str
    description: str = ""
    arguments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def required_arguments(self) -> list[str]:
        """Prompt argument names declared as required."""
        return [str(a.get("name")) for a in self.arguments if a.get("required") is True]


@dataclass
class Manifest:
    """Everything a server advertises, in a deterministic shape."""

    server_name: str
    server_version: str
    protocol_version: str
    capabilities: dict
    instructions: str | None
    tools: list[Tool]
    resources: list[Resource]
    resource_templates: list[ResourceTemplate]
    prompts: list[Prompt]
    warnings: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a JSON-ready dict with stable key ordering."""
        return {
            "manifestVersion": MANIFEST_FORMAT_VERSION,
            "generator": "mcp-doctor " + __version__,
            "server": {
                "name": self.server_name,
                "version": self.server_version,
                "protocolVersion": self.protocol_version,
            },
            "capabilities": self.capabilities,
            "instructions": self.instructions,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "signature": tool.signature(),
                    "inputSchema": tool.input_schema,
                    "outputSchema": tool.output_schema,
                    "annotations": tool.annotations,
                }
                for tool in self.tools
            ],
            "resources": [
                {
                    "uri": item.uri,
                    "name": item.name,
                    "description": item.description,
                    "mimeType": item.mime_type,
                    "size": item.size,
                }
                for item in self.resources
            ],
            "resourceTemplates": [
                {
                    "uriTemplate": item.uri_template,
                    "name": item.name,
                    "description": item.description,
                    "mimeType": item.mime_type,
                }
                for item in self.resource_templates
            ],
            "prompts": [
                {
                    "name": prompt.name,
                    "description": prompt.description,
                    "arguments": prompt.arguments,
                }
                for prompt in self.prompts
            ],
            "warnings": self.warnings,
            "undeclaredCapabilities": self.undeclared,
        }

    def to_json(self) -> str:
        """Deterministic JSON text, sorted keys, two-space indent."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def counts(self) -> dict[str, int]:
        """Counts shown in the inspect header."""
        return {
            "tools": len(self.tools),
            "resources": len(self.resources),
            "resourceTemplates": len(self.resource_templates),
            "prompts": len(self.prompts),
        }


def _probe_undeclared(peer: Peer, capabilities: dict) -> set[str]:
    """Ask for lists the server did not advertise.

    A server that declares no tools capability but happily answers tools/list is
    misreporting itself, and a strict client will never call it. Finding that
    requires asking anyway, so the probe runs here and the discovery is recorded
    as a warning plus (in the audit) as a finding.
    """
    discovered: set[str] = set()
    for capability, method in (
        ("tools", "tools/list"),
        ("resources", "resources/list"),
        ("prompts", "prompts/list"),
    ):
        if capability in capabilities:
            continue
        response = peer.try_request(method)
        if "error" in response:
            continue
        result = response.get("result")
        if (
            isinstance(result, dict)
            and isinstance(result.get(capability), list)
            and result[capability]
        ):
            discovered.add(capability)
    return discovered


def build_manifest(peer: Peer) -> Manifest:
    """Query a connected peer and build its manifest.

    Capability-gated: a server that does not advertise tools is not asked for
    tools, so a resource-only server does not report a spurious error. If the
    server advertises a capability but then fails the list call, the failure is
    recorded in warnings and the command still produces a manifest.
    """
    info = peer.server_info
    if info is None:
        raise RuntimeError("build_manifest requires an initialized peer")
    capabilities = info.capabilities if isinstance(info.capabilities, dict) else {}
    warnings: list[str] = list(peer.warnings)

    tools: list[Tool] = []
    resources: list[Resource] = []
    templates: list[ResourceTemplate] = []
    prompts: list[Prompt] = []

    hidden = _probe_undeclared(peer, capabilities)
    if hidden:
        warnings.append(
            "server answered list calls for undeclared capabilities: " + ", ".join(sorted(hidden))
        )
    disclosed = set(capabilities) | hidden

    if "tools" in disclosed:
        items, errors = peer.list_tools()
        warnings.extend(errors)
        for item in items:
            tools.append(
                Tool(
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "") or ""),
                    input_schema=item.get("inputSchema")
                    if isinstance(item.get("inputSchema"), dict)
                    else {},
                    output_schema=item.get("outputSchema")
                    if isinstance(item.get("outputSchema"), dict)
                    else None,
                    annotations=item.get("annotations")
                    if isinstance(item.get("annotations"), dict)
                    else {},
                    raw=item,
                )
            )

    if "resources" in disclosed:
        items, errors = peer.list_resources()
        warnings.extend(errors)
        for item in items:
            resources.append(
                Resource(
                    uri=str(item.get("uri", "")),
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "") or ""),
                    mime_type=str(item.get("mimeType", "") or ""),
                    size=item.get("size") if isinstance(item.get("size"), int) else None,
                    raw=item,
                )
            )
        items, errors = peer.list_resource_templates()
        warnings.extend(errors)
        for item in items:
            templates.append(
                ResourceTemplate(
                    uri_template=str(item.get("uriTemplate", "")),
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "") or ""),
                    mime_type=str(item.get("mimeType", "") or ""),
                    raw=item,
                )
            )

    if "prompts" in disclosed:
        items, errors = peer.list_prompts()
        warnings.extend(errors)
        for item in items:
            arguments = item.get("arguments")
            prompts.append(
                Prompt(
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "") or ""),
                    arguments=[a for a in arguments if isinstance(a, dict)]
                    if isinstance(arguments, list)
                    else [],
                    raw=item,
                )
            )

    return Manifest(
        server_name=info.server_name,
        server_version=info.server_version,
        protocol_version=info.protocol_version,
        capabilities=capabilities,
        instructions=info.instructions,
        tools=tools,
        resources=resources,
        resource_templates=templates,
        prompts=prompts,
        warnings=warnings,
        undeclared=sorted(hidden),
    )


def manifest_document(manifest: Manifest) -> dict[str, Any]:
    """Alias kept for callers that prefer an explicit name over the method."""
    return manifest.to_dict()
