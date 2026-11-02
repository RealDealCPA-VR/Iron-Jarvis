"""v1.424.0 (review): a model is refused ONLY on positive evidence.

The lane's first cut refused a model Codex's list did not hold BEFORE the
Iron-Proxy lease, reading ``<CODEX_HOME or ~/.codex>/models_cache.json`` —
the DAEMON's home. Two ways that refused a model Codex WOULD run:

* the call runs on an Iron-Proxy Codex account, whose OWN home holds its OWN
  list (another plan — a Pro account lists models a Plus one does not); the
  daemon's home said nothing about it;
* a list Codex wrote days ago (Codex rewrites it whenever it starts and its
  copy is older than five minutes, so an old file means Codex has not run
  there since): a model released meanwhile, or a login changed since (an API
  key runs models a plan does not), was refused on a stale reading.

Now the list consulted is the one of the home the call REALLY runs in (the
lease's, after the lease), and only while it is recent
(``codex_models.EVIDENCE_MAX_AGE_S``); a name matching a listed model in
another case is that model. Driven through the REAL adapter against the fake
app-server (``fixtures/fake_codex_app_server_v1365.py``) and the in-memory
Iron-Proxy of ``test_iron_proxy_accounts_v1301``.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from iron_jarvis.iron_proxy import accounts
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers import codex_models as CM
from iron_jarvis.providers.adapters.codex_native import adapter as A
from iron_jarvis.providers.adapters.codex_native import session as S
from iron_jarvis.providers.cli_auth import CliAuthProbe
from tests.test_codex_native_v1365 import Fake, _kill_all_sessions, _user

FIXTURE = Path(__file__).parent / "fixtures" / "codex_models_cache_v14240.json"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    S.reset_verdict()
    _kill_all_sessions()
    monkeypatch.setattr(S, "START_TIMEOUT_S", 120.0)
    monkeypatch.setattr(A, "_ENV_NOTED", False)
    monkeypatch.setattr(A, "_NOTED_REASONS", set())
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    reading = tmp_path / "reading-home"
    reading.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(reading))
    previous = accounts.set_client_source(lambda: None)
    CM.reset()
    yield reading
    CM.reset()
    accounts.set_client_source(previous)
    _kill_all_sessions()
    S.reset_verdict()


def _write_list(home: Path, *, extra: tuple[str, ...] = (), fetched_at: str | None = None) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base = data["models"][0]
    for slug in extra:
        row = dict(base, slug=slug, display_name=slug.upper(), priority=1)
        data["models"].append(row)
    if fetched_at is not None:
        data["fetched_at"] = fetched_at
    home.mkdir(parents=True, exist_ok=True)
    (home / "models_cache.json").write_text(json.dumps(data), encoding="utf-8")


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


ONE_ANSWER = [{"steps": [{"text": "hi from codex"}]}]


async def test_a_model_the_leased_accounts_own_list_holds_is_never_refused(tmp_path, monkeypatch, _clean):
    """The daemon's home (a Plus list) does not hold gpt-6-pro; the Iron-Proxy
    account the call runs on (a Pro list) does: Codex runs it there."""
    from tests.test_iron_proxy_accounts_v1301 import FakeIronProxy, _homes

    _write_list(_clean)
    assert CM.read_catalog() is not None and CM.runs("gpt-6-pro") is False
    homes = _homes(tmp_path, "PRO")
    _write_list(Path(homes["PRO"]), extra=("gpt-6-pro",))
    client = FakeIronProxy("openai", homes)
    accounts.set_client_source(lambda: client)
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    out = await fake.adapter(model="gpt-6-pro").complete(system="", messages=[_user()], tools=[])
    assert out.text == "hi from codex"
    starts = fake.starts()
    assert [r["env"].get("CODEX_HOME") for r in starts] == [homes["PRO"]]
    assert client.picks == ["PRO"]


async def test_a_model_the_leased_accounts_own_fresh_list_lacks_is_refused_naming_its_models(
    tmp_path, monkeypatch, _clean
):
    """Positive evidence on the ACCOUNT's own list: refused in words, the
    account's own models named, Codex never started."""
    from tests.test_iron_proxy_accounts_v1301 import FakeIronProxy, _homes

    homes = _homes(tmp_path, "PRO")
    _write_list(Path(homes["PRO"]), extra=("gpt-6-pro",))
    client = FakeIronProxy("openai", homes)
    accounts.set_client_source(lambda: client)
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    with pytest.raises(A.CodexModelNotOffered) as caught:
        await fake.adapter(model="gpt-4o").complete(system="", messages=[_user()], tools=[])
    assert caught.value.no_failover is True
    assert "gpt-4o" in str(caught.value) and "GPT-6-PRO" in str(caught.value)
    assert fake.starts() == []


async def test_a_stale_list_is_not_evidence(tmp_path, monkeypatch, _clean):
    """A list Codex wrote ten days ago does not say what it runs today."""
    _write_list(_clean, fetched_at=_iso(-timedelta(days=10)))
    assert CM.read_catalog() is not None
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    out = await fake.adapter(model="gpt-6-nova").complete(system="", messages=[_user()], tools=[])
    assert out.text == "hi from codex" and len(fake.starts()) == 1


async def test_a_stale_list_by_its_file_time_is_not_evidence_either(tmp_path, monkeypatch, _clean):
    """No ``fetched_at`` in the file: its modification time decides."""
    _write_list(_clean)
    old = time.time() - 10 * 86400
    os.utime(_clean / "models_cache.json", (old, old))
    assert CM.read_catalog() is not None
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    out = await fake.adapter(model="gpt-6-nova").complete(system="", messages=[_user()], tools=[])
    assert out.text == "hi from codex"


async def test_a_fresh_list_still_refuses_and_nothing_starts(tmp_path, monkeypatch, _clean):
    """The control: a fresh reading of the home the call runs in (no
    Iron-Proxy) refuses a model it does not hold, before Codex starts."""
    _write_list(_clean, fetched_at=_iso(-timedelta(minutes=3)))
    assert CM.read_catalog() is not None
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    with pytest.raises(A.CodexModelNotOffered):
        await fake.adapter(model="gpt-4o").complete(system="", messages=[_user()], tools=[])
    assert fake.starts() == []


async def test_another_case_of_a_listed_model_is_that_model(tmp_path, monkeypatch, _clean):
    _write_list(_clean)
    assert CM.read_catalog() is not None
    assert A.not_offered_error("GPT-6-Sol") is None
    assert CM.runs("GPT-6-Sol") is True


def test_the_home_of_a_lease_is_the_home_its_codex_runs_in():
    """An account's own folder (CODEX_HOME set), an ADOPTED account (the
    variable unset: Codex's own default — in the suite, the suite's folder,
    never the developer's real ~/.codex), or none (this app's own)."""
    from iron_jarvis.iron_proxy.accounts import Lease

    own = Lease(profile_id="a", title="A", provider="openai", env_set={"CODEX_HOME": "C:/homes/a"},
                ij_provider="codex-cli")
    adopted = Lease(profile_id="b", title="B", provider="openai", env_unset=("OPENAI_API_KEY", "CODEX_HOME"),
                    ij_provider="codex-cli")
    assert A._lease_home(own) == "C:/homes/a"
    assert A._lease_home(adopted) == str(CM.cli_default_home())
    assert Path(A._lease_home(adopted)) != Path.home() / ".codex"
    assert A._lease_home(None) is None


async def test_a_model_codex_just_added_is_never_refused_on_the_cached_reading(tmp_path, monkeypatch, _clean):
    """The cached reading can be up to TTL_S behind the file; Codex rewrote
    its list a moment ago with a new model: the refusal is confirmed on the
    file as it is NOW, so the new model is sent."""
    _write_list(_clean)
    assert CM.read_catalog() is not None and CM.runs("gpt-6-nova") is False
    _write_list(_clean, extra=("gpt-6-nova",))  # Codex just added it
    assert A.not_offered_error("gpt-6-nova") is None
    fake = Fake(tmp_path, monkeypatch, ONE_ANSWER)
    out = await fake.adapter(model="gpt-6-nova").complete(system="", messages=[_user()], tools=[])
    assert out.text == "hi from codex"
