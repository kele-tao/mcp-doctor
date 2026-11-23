"""Manifest tests: deterministic export and typed views over the surface."""

from __future__ import annotations

import json

from mcp_doctor.client import connect_stdio
from mcp_doctor.manifest import build_manifest
from mcp_doctor.report import render_manifest_markdown


def test_demo_manifest_shape(demo_command):
    peer = connect_stdio(demo_command)
    try:
        manifest = build_manifest(peer)
    finally:
        peer.close()
    assert manifest.server_name == "demo-tools"
    assert [tool.name for tool in manifest.tools] == ["add", "echo", "word_count"]
    assert manifest.tools[0].required == ["a", "b"]
    assert manifest.tools[0].signature() == "add(a: integer, b: integer) -> object"
    assert manifest.resources[0].uri == "memo://handbook"
    assert manifest.counts()["tools"] == 3
    assert manifest.prompts == []


def test_manifest_json_is_deterministic(demo_command):
    outputs = []
    for _ in range(2):
        peer = connect_stdio(demo_command)
        try:
            outputs.append(build_manifest(peer).to_json())
        finally:
            peer.close()
    assert outputs[0] == outputs[1]
    document = json.loads(outputs[0])
    assert document["manifestVersion"] == "1"
    assert document["tools"][0]["signature"].startswith("add(")
    assert "generatedAt" not in outputs[0]


def test_markdown_reference_doc(demo_command):
    peer = connect_stdio(demo_command)
    try:
        text = render_manifest_markdown(build_manifest(peer))
    finally:
        peer.close()
    assert text.startswith("# demo-tools 1.0.0")
    assert "## Tools" in text
    assert "### add" in text
    assert "| Argument | Type | Required | Description |" in text
    assert "memo://handbook" in text


def test_tool_complexity_is_positive(demo_command):
    peer = connect_stdio(demo_command)
    try:
        manifest = build_manifest(peer)
    finally:
        peer.close()
    assert all(tool.complexity() > 0 for tool in manifest.tools)
    assert manifest.tools[0].complexity() >= 2
