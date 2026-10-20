"""v1.350.1 — two user reports, both found on the live install.

1. "I attempted to use the sequentialthinking tool": npm error 404 for
   @modelcontextprotocol/server-sequentialthinking. The prebuilt catalog in
   daemon/app.py named a package npm never had (the real one is
   server-sequential-thinking; connectors/catalog.py had it right). Fixed at
   the source, repaired in installed packs at boot, and a registry 404 now
   reads "the package name is wrong" instead of "read its last output".

2. "the options for that specific connection [RTX6000ada] … it is picking
   up historical models this endpoint has. Only true endpoint availability
   should appear." The endpoint is a LiteLLM proxy whose /v1/models answers
   seven ALIASES that route to two servers, one of them an embedding model.
   The picker now offers one row per real deployment (old names ride the
   row's aliases) and no embedding models.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace as N

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.fleet.models import FleetNode
from iron_jarvis.fleet.picker import looks_like_embedding, picker_rows
from iron_jarvis.mcp.package_fixes import RENAMED_PACKAGES, repair_known_package_names
from iron_jarvis.mcp.tools import classify_load_failure
from iron_jarvis.templates import analyze_requirements

ROOT = Path(__file__).resolve().parents[1]
BAD = "@modelcontextprotocol/server-sequentialthinking"
GOOD = "@modelcontextprotocol/server-sequential-thinking"

# --------------------------------------------------------------------------- #
# 1. the pack name
# --------------------------------------------------------------------------- #


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_the_bad_name_is_gone_and_there_is_one_catalog_that_names_the_real_package():
    """v1.357.0 (G82, moved on purpose): this pin compared the TWO catalogs
    (daemon/app.py's table and connectors/catalog.py) because their drift
    shipped the bad name. There is one catalog now: app.py names no package
    at all and the Tools page's rows are built from connectors/catalog.py."""
    app_src = _src("src/iron_jarvis/daemon/app.py")
    cat_src = _src("src/iron_jarvis/connectors/catalog.py")
    assert BAD not in app_src and BAD not in cat_src
    assert GOOD in cat_src
    # No second table: app.py spells no MCP package and hands the route the
    # one catalog's rows.
    assert not re.findall(r'"(@modelcontextprotocol/[\w.-]+)"', app_src)
    assert "_MCP_CATALOG = _mcp_catalog_rows()" in app_src
    from iron_jarvis.connectors.catalog import mcp_catalog_rows

    (row,) = [r for r in mcp_catalog_rows() if r["id"] == "sequentialthinking"]
    assert GOOD in row["args"]


class _Cfg:
    def __init__(self, home: Path, servers):
        self.home = home
        self.mcp_servers = servers


def test_an_installed_pack_with_the_bad_name_is_repaired_and_persisted(tmp_path, monkeypatch):
    written: list[dict] = []
    monkeypatch.setattr(
        "iron_jarvis.mcp.package_fixes.persist_config_values",
        lambda home, values: written.append(values),
    )
    cfg = _Cfg(
        tmp_path,
        [
            {"name": "sequentialthinking", "command": "npx", "args": ["-y", BAD], "env": {"A": "1"}},
            {"name": "pinned", "command": "npx", "args": ["-y", BAD + "@0.6.2"]},
            {"name": "other", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
            {"name": "mine", "command": "npx", "args": ["-y", BAD + "-fork"]},  # a user's own: untouched
            "not-a-dict",
        ],
    )
    assert repair_known_package_names(config=cfg) == ["sequentialthinking", "pinned"]
    assert cfg.mcp_servers[0]["args"] == ["-y", GOOD] and cfg.mcp_servers[0]["env"] == {"A": "1"}
    assert cfg.mcp_servers[1]["args"] == ["-y", GOOD + "@0.6.2"]
    assert cfg.mcp_servers[2]["args"] == ["-y", "@modelcontextprotocol/server-memory"]
    assert cfg.mcp_servers[3]["args"] == ["-y", BAD + "-fork"]
    assert written and written[0]["mcp_servers"] == cfg.mcp_servers
    # Idempotent: the next boot finds nothing to do and writes nothing.
    assert repair_known_package_names(config=cfg) == []
    assert len(written) == 1
    assert RENAMED_PACKAGES[BAD] == GOOD


def test_the_daemon_boot_repairs_a_pack_already_in_config_toml(tmp_path):
    app = create_app(str(tmp_path))
    home = Path(app.state.platform.config.home)
    from iron_jarvis.core.config import persist_config_values

    persist_config_values(
        home, {"mcp_servers": [{"name": "sequentialthinking", "command": "npx", "args": ["-y", BAD], "enabled": False}]}
    )
    again = create_app(str(tmp_path))
    servers = again.state.platform.config.mcp_servers
    (st,) = [s for s in servers if s.get("name") == "sequentialthinking"]
    assert st["args"] == ["-y", GOOD]
    assert BAD not in (home / "config.toml").read_text(encoding="utf-8")


USER_ERROR = (
    "MCPServerStopped: pack 'sequentialthinking': the MCP server closed the connection (exit code 1); "
    "its last output: npm error code E404 | npm error 404 Not Found - GET "
    "https://registry.npmjs.org/@modelcontextprotocol%2fserver-sequentialthinking - Not found | npm error 404 | "
    "npm error 404 '@modelcontextprotocol/server-sequentialthinking@*' is not in this registry. | npm error 404"
)


def test_a_registry_404_reads_as_a_wrong_package_name_naming_it():
    out = classify_load_failure({"command": "npx"}, USER_ERROR)
    assert out["reason"].startswith(f"the package {BAD} is not in its registry")
    assert "correct the package name" in out["fix"]
    uv = classify_load_failure(
        {"command": "uvx"},
        "exit code 2; its last output: x No solution found | Because mcp-server-nope was not found in the package registry and",
    )
    assert "mcp-server-nope is not in its registry" in uv["reason"]
    # An ordinary crash keeps the crash words (the new branch is narrow).
    crash = classify_load_failure({"command": "npx"}, "the MCP server closed the connection (exit code 1); its last output: missing key")
    assert crash["reason"] == "the extension's server exited (exit code 1)"


# --------------------------------------------------------------------------- #
# 2. the endpoint picker
# --------------------------------------------------------------------------- #

IDS = ["brain (RTX)", "embed (RTX)", "brain", "embed", "qwen3.6:27b", "gemma4:26b", "mxbai-embed-large"]
VB, VE = "http://vllm-brain:8000/v1", "http://vllm-embed:8000/v1"
ROUTES = [
    ("brain (RTX)", "hosted_vllm/brain", VB),
    ("embed (RTX)", "hosted_vllm/mxbai-embed-large", VE),
    ("brain", "hosted_vllm/brain", VB),
    ("embed", "hosted_vllm/mxbai-embed-large", VE),
    ("qwen3.6:27b", "hosted_vllm/brain", VB),
    ("gemma4:26b", "hosted_vllm/brain", VB),
    ("mxbai-embed-large", "hosted_vllm/mxbai-embed-large", VE),
]
KIDS = [N(alias=a, default_model=m, base_url=u) for a, m, u in ROUTES]


def test_one_row_per_real_deployment_and_no_embedding_models():
    assert picker_rows(IDS, default_model="brain (RTX)", children=KIDS) == [
        {"model": "brain (RTX)", "aliases": ["brain", "qwen3.6:27b", "gemma4:26b"]}
    ]
    # No default among the names: the deployment's own id wins.
    assert picker_rows(IDS, default_model="", children=KIDS)[0]["model"] == "brain"
    # Two servers for one model id are two deployments.
    kids2 = KIDS + [N(alias="brain-b", default_model="hosted_vllm/brain", base_url="http://other:8000/v1")]
    rows = picker_rows(IDS + ["brain-b"], default_model="brain (RTX)", children=kids2)
    assert [r["model"] for r in rows] == ["brain (RTX)", "brain-b"]


def test_without_topology_names_are_kept_but_embeddings_still_go_and_the_default_never_does():
    rows = picker_rows(IDS, default_model="brain (RTX)", children=[])
    assert [r["model"] for r in rows] == ["brain (RTX)", "brain", "qwen3.6:27b", "gemma4:26b"]
    # A default the user chose is never dropped, even if it looks like one.
    assert picker_rows(["embed"], default_model="embed", children=[]) == [{"model": "embed", "aliases": []}]
    # ... also beside other names (the empty-list fallback must not be what keeps it).
    assert [r["model"] for r in picker_rows(["embed", "brain"], default_model="embed", children=[])] == ["embed", "brain"]
    # Nothing left: the default is offered (the old fallback).
    assert picker_rows(["nomic-embed-text"], default_model="", children=[]) == [
        {"model": "nomic-embed-text", "aliases": []}
    ]


@pytest.mark.parametrize(
    "name,embed",
    [
        ("mxbai-embed-large", True), ("nomic-embed-text", True), ("embed (RTX)", True), ("bge-m3", True),
        ("text-embedding-3-small", True), ("qwen3-embedding-8b", True), ("e5-large", True), ("bge-reranker-v2", True),
        ("brain (RTX)", False), ("qwen3.6:27b", False), ("gemma4:26b", False), ("llama3.1:8b", False),
        ("gpt-oss-20b", False), ("member", False), ("deepseek-r1", False), ("mistral-7b-instruct", False),
    ],
)
def test_embedding_names(name, embed):
    assert looks_like_embedding(name) is embed


def test_models_route_offers_the_proxy_s_real_models_and_templates_accept_old_names(tmp_path, monkeypatch):
    from iron_jarvis.providers import discovery

    real = discovery.discover_models

    def fake(provider, cred, base_url="", **kw):
        if str(provider).startswith("fleet-"):
            return list(IDS)
        return real(provider, cred, base_url=base_url, **kw)

    monkeypatch.setattr(discovery, "discover_models", fake)
    client = TestClient(create_app(str(tmp_path)))
    node = client.post(
        "/fleet/nodes",
        json={"base_url": "http://127.0.0.1:9/v1", "label": "RTX6000ada", "routable": True, "default_model": "brain (RTX)"},
    ).json()["node"]
    fleet = client.app.state.platform.fleet
    fleet.absorb_children(
        node["id"],
        [
            FleetNode(id=f"{node['id']}-{i}", label=a, base_url=u, kind="unknown", source="topology",
                      parent_id=node["id"], alias=a, default_model=m, routable=False)
            for i, (a, m, u) in enumerate(ROUTES)
        ],
    )
    rows = [m for m in client.get("/models").json()["models"] if m["provider"] == f"fleet-{node['id']}"]
    assert [r["model"] for r in rows] == ["brain (RTX)"], rows
    assert rows[0]["aliases"] == ["brain", "qwen3.6:27b", "gemma4:26b"]
    assert rows[0]["name"] == "RTX6000ada"
    # A template pinned to an old name is still "connected".
    reqs = analyze_requirements(
        "task", f"fleet-{node['id']}", "qwen3.6:27b", None,
        selectable_models=rows, live_tools=[], has_secret=lambda n: False, comm_config={}, agent_names=[],
    )
    (model_req,) = [r for r in reqs if r["key"] == "model"]
    assert model_req["ok"] is True


def test_the_model_menu_panel_fits_the_window():
    from_page = _src("dashboard/app/chat/page.tsx")
    assert 'testId="model-sub"' in from_page and "<FitPanel" in from_page
    assert "max-h-[24rem]" not in from_page
