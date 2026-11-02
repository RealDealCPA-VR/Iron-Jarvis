"""v1.426.0 (Wave 6 lane B, E5): THE ONE TABLE of the subscription CLIs.

``providers/subscription_clis.py`` holds every fact the app keeps about
Claude Code, Codex and the Grok CLI; a dozen modules used to type their own
copy. Each derived table below is pinned to its OLD literal value (copied
from the code before the change): the refactor changes NO behaviour. The
dashboard's twin (``dashboard/lib/subscriptionClis.ts``) is GENERATED from the
table and must be current.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from iron_jarvis.providers import subscription_clis as SUBS

ROOT = Path(__file__).resolve().parents[1]


def test_every_derived_table_keeps_its_old_value():
    from iron_jarvis.agents import model_team as MT
    from iron_jarvis.hosted import words as W
    from iron_jarvis.iron_proxy import accounts as A
    from iron_jarvis.iron_proxy import service as IS
    from iron_jarvis.providers import cli_auth as CA
    from iron_jarvis.providers import failure_words as FW

    # iron_proxy/accounts (v1.301.0 / v1.302.0 / v1.368.0)
    assert {k: (v.ij_provider, v.proxy_provider, v.home_var, v.label) for k, v in A.PROVIDERS.items()} == {
        "claude-cli": ("claude-cli", "anthropic", "CLAUDE_CONFIG_DIR", "Claude"),
        "codex-cli": ("codex-cli", "openai", "CODEX_HOME", "ChatGPT (Codex)"),
        "grok-cli": ("grok-cli", "xai", "GROK_HOME", "Grok"),
    }
    assert list(A.PROVIDERS) == ["claude-cli", "codex-cli", "grok-cli"]
    assert A.VENDOR_OF == {"claude-cli": "claude", "codex-cli": "codex", "grok-cli": "grok"}
    assert A._DEFAULT_HOME_DIRS == {"CLAUDE_CONFIG_DIR": ".claude", "CODEX_HOME": ".codex", "GROK_HOME": ".grok"}
    # iron_proxy/service
    assert IS.JARVIS_TO_PROXY == {"claude-cli": "anthropic", "codex-cli": "openai", "grok-cli": "xai"}
    # hosted/words
    assert W.PROVIDERS == {"claude-code": "claude-cli", "codex": "codex-cli", "grok": "grok-cli"}
    assert W.PROXY == {"claude-code": "anthropic", "codex": "openai", "grok": "xai"}
    # agents/model_team
    assert MT._PLAN_CLIS == frozenset({"claude-cli", "codex-cli", "grok-cli"})
    assert MT._CLI_LABELS == {"claude-cli": "Claude Code", "codex-cli": "Codex", "grok-cli": "Grok"}
    # providers/failure_words
    assert FW.SIGN_IN_CLIS == frozenset({"claude-cli", "codex-cli", "grok-cli"})
    assert list(FW.PLAIN_NAMES)[:4] == ["claude-cli", "codex-cli", "grok-cli", "opencode-cli"]
    assert {k: FW.PLAIN_NAMES[k] for k in ("claude-cli", "codex-cli", "grok-cli")} == {
        "claude-cli": "Claude Code", "codex-cli": "Codex", "grok-cli": "Grok CLI"}
    # providers/cli_auth (Grok is NEVER probed: its session file is read)
    assert CA.CLI_BINARIES == {"claude-cli": "claude", "codex-cli": "codex"}
    assert CA.SIGN_IN_BINARIES == {"claude-cli": "claude", "codex-cli": "codex", "grok-cli": "grok"}


def test_the_cli_kind_answers_are_unchanged():
    """The "is this a subscription CLI" tuples in the provider manager's
    health rows and the /models catalog read the table now."""
    from iron_jarvis.daemon.routes import connections as C
    from iron_jarvis.providers import manager as M

    for mod in (C, M):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert '("claude-cli", "codex-cli", "grok-cli")' not in src, mod.__name__
        assert "_SUBS.PROVIDER_SET" in src, mod.__name__
    assert SUBS.PROVIDER_SET == frozenset({"claude-cli", "codex-cli", "grok-cli"})


def test_the_table_reads_both_ways():
    assert SUBS.by_provider("codex-cli").harness == "codex"
    assert SUBS.by_harness("grok").provider == "grok-cli"
    assert SUBS.by_provider("anthropic") is None and SUBS.by_harness("pi") is None
    assert [c.provider for c in SUBS.SUBSCRIPTION_CLIS] == list(SUBS.PROVIDERS)


def test_the_dashboard_twin_is_generated_and_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_subscription_clis.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    twin = (ROOT / "dashboard" / "lib" / "subscriptionClis.ts").read_text(encoding="utf-8")
    assert "DO NOT EDIT BY HAND" in twin
    for c in SUBS.SUBSCRIPTION_CLIS:
        assert f'"provider": "{c.provider}"' in twin and f'"proxy": "{c.proxy}"' in twin


def test_the_dashboard_reads_the_twin_where_it_used_to_type_the_rows():
    iron = (ROOT / "dashboard" / "lib" / "ironProxy.ts").read_text(encoding="utf-8")
    onb = (ROOT / "dashboard" / "lib" / "onboarding.ts").read_text(encoding="utf-8")
    assert "PROXY_TO_PROVIDER" in iron and 'xai: "grok-cli"' not in iron
    friendly = onb[onb.index("const FRIENDLY") : onb.index("};", onb.index("const FRIENDLY"))]
    assert "...SUBSCRIPTION_CLI_NAMES" in friendly
    assert not re.search(r'"(claude|codex|grok)-cli":', friendly)
