"""v1.422.0 review follow-up (Wave 2 "Never lose work", B1): the benchmark's
identity moves with the round's patience.

Every harness turn — the benchmark's Jarvis runner included — waits out a
passing provider failure and asks the same call again (``harness/patience``):
a run that would have been "did not run" can now finish, and the rules of
which failures are waited out decide that. The module is in neither the
lead's specs nor its fingerprint, so a change to it alone must be a new
harness for the benchmark, never a reuse of results made under other rules
(the way Wave 1 added ``harness/standard`` and ``harness/command_env``).
``providers/retry_round`` only marks a ``provider.failed`` notice — it changes
nothing a run does — and stays out.
"""

from __future__ import annotations

from iron_jarvis.platform import build_platform


def test_the_bench_identity_moves_with_the_rounds_patience(tmp_path, monkeypatch):
    from iron_jarvis.harness_bench import runners as R

    platform = build_platform(str(tmp_path / "root"))
    before = R.JarvisRunner(platform, provider="p", model="m", round_budget=60).identity()["harness_sha256"]
    real = R._module_bytes

    def changed(mod):
        out = real(mod)
        return out + b"# changed" if getattr(mod, "__name__", "").endswith("harness.patience") else out

    monkeypatch.setattr(R, "_module_bytes", changed)
    after = R.JarvisRunner(platform, provider="p", model="m", round_budget=60).identity()["harness_sha256"]
    assert after != before, "a change to harness/patience kept the bench's identity"
