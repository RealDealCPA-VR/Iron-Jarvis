"""One policy object, every holder current (v1.235.0).

Decision D01 says the Browser capability reuses computer use's risk primitives rather
than growing a second, competing policy engine. `platform.py` implements that literally:
`BrowserRuntime` is built with the SAME `ComputerUsePolicy` instance that `CUContext`
holds, so a domain allowlist the user edits once applies to both.

That sharing turned an invisible habit into a defect. `POST /computeruse/enable` used to
REBIND `cu.policy` to a freshly constructed policy. While computer use was the only
holder, rebinding and mutating were indistinguishable. With a second holder they are
not: the browser kept its boot-time object, so it went on enforcing the allowlist the
user had just replaced, and no screen anywhere would have said so. The route now copies
the new values ONTO the existing instance.

What each assertion catches:

* IDENTITY. If the route rebinds again, `platform.browser.policy is platform.computeruse.policy`
  breaks — and that is the only cheap signal, because both objects would hold plausible
  values and every existing computer-use test would stay green.
* PROPAGATION. Identity alone is not enough: a future refactor could keep one object and
  update a copy. This asserts the browser's view of the allowlist actually changes.
* THE ROUTE STILL WORKS. Mutating in place must not break what the endpoint reports, so
  the response is checked too — a fix that quietly stopped applying the change would
  otherwise read as success.

Deliberately NOT asserted: that the browser tools consult the policy. They do not, yet —
escalation lands in Ship 3 (plan section 8.2). Pinning a consultation that does not exist
would be the kind of aspirational test this project treats as a lie.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def test_the_browser_and_computer_use_share_one_policy_object(tmp_path):
    # tmp_path, never TemporaryDirectory: on Windows the open SQLite handle makes
    # eager cleanup raise WinError 32, which is why every test in this repo takes
    # the fixture and lets pytest reap the directory later.
    app = create_app(str(tmp_path))
    p = app.state.platform
    assert p.browser.policy is p.computeruse.policy, (
        "D01: the Browser capability must hold the SAME policy instance as computer "
        "use, or the two features can disagree about what is allowed"
    )


def test_enabling_computer_use_does_not_strand_the_browsers_policy(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    before = p.browser.policy
    with TestClient(app) as c:
        r = c.post(
            "/computeruse/enable",
            json={"enabled": False, "domain_allowlist": ["irs.gov"]},
        )
        assert r.status_code == 200, r.text
    # Identity survived the route: nothing was rebound.
    assert p.browser.policy is before
    assert p.browser.policy is p.computeruse.policy
    # And the browser's view really changed, rather than holding a stale copy.
    assert list(p.browser.policy.domain_allowlist) == ["irs.gov"], (
        "the browser is holding a stale allowlist — this is the exact defect the "
        "in-place update exists to prevent"
    )


def test_a_second_enable_call_keeps_the_same_object_and_the_newest_values(tmp_path):
    """Two edits in a row: the second must not resurrect the first's values."""
    app = create_app(str(tmp_path))
    p = app.state.platform
    before = p.browser.policy
    with TestClient(app) as c:
        c.post("/computeruse/enable", json={"enabled": False, "domain_allowlist": ["a.test"]})
        c.post("/computeruse/enable", json={"enabled": False, "domain_allowlist": ["b.test"]})
    assert p.browser.policy is before
    assert list(p.browser.policy.domain_allowlist) == ["b.test"]
