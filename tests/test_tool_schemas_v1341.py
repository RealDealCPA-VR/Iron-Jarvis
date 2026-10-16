"""v1.341.0: every tool the model can see advertises its arguments.

Found while surveying the chat lane as a harness: the five settings tools
(`config_list`, `config_set`, `config_change`, `config_change_protected`,
`config_secret`) declared their argument schema under ``parameters`` — a name
``Tool.spec()`` never reads — so since v1.321.0 every model was shown
``input_schema: {}`` for them and had to guess ``key``/``value`` from the
description; the registry's required-argument check (v1.228.0) was blind for
them too. A tool with no schema is a tool a weaker model cannot call.

The first test is registry-wide: it builds the real platform and refuses any
registered tool whose spec is not a JSON-schema object, so the next
mis-named attribute fails here rather than in a user's chat. The second
pins the five by name, so the registry-wide one cannot be "fixed" by
dropping them from the registry.
"""

from __future__ import annotations

import pytest

from iron_jarvis.settings.credentials import ConfigSecretTool
from iron_jarvis.settings.tools import (
    ConfigChangeProtectedTool,
    ConfigChangeTool,
    ConfigListTool,
    ConfigSetTool,
)
from iron_jarvis.tools.base import Tool


def test_every_registered_tool_advertises_an_object_schema(platform):
    registry = platform.registry
    names = registry.names()
    assert len(names) > 100, names  # the real registry, not a stub
    bare = []
    for name in names:
        tool = registry.get(name)
        schema = tool.spec().get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            bare.append((name, schema))
    assert bare == [], f"tools that tell the model nothing about their arguments: {bare}"


@pytest.mark.parametrize(
    "cls, required",
    [
        (ConfigListTool, []),
        (ConfigSetTool, ["key", "value"]),
        (ConfigChangeTool, ["key", "value"]),
        (ConfigChangeProtectedTool, ["key", "value"]),
        (ConfigSecretTool, ["name"]),
    ],
)
def test_the_settings_tools_declare_their_arguments(cls, required):
    assert "parameters" not in vars(cls), "declared under the wrong name — spec() reads input_schema"
    schema = cls.input_schema
    assert schema is not Tool.input_schema, "inherited the empty default"
    assert schema.get("type") == "object"
    assert "properties" in schema and schema["properties"]
    assert list(schema.get("required", [])) == required
