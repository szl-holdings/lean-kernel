#!/usr/bin/env python3
"""Served-theorem ↔ real Lean declaration guard test.

FAILS if any theorem name ``app/server.py`` serves as a confirming theorem does
not resolve to a real Lean ``theorem`` / ``lemma`` declaration in the kernel
corpus (an unbacked overclaim), and FAILS if any served name asserts
Λ-uniqueness — that stays Conjecture 1 and is never served as proven.

Runs under pytest and directly (``python3 tests/test_theorem_registry_guard.py``).
"""

from __future__ import annotations

import importlib.util
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_TEST_DIR)
_GUARD_PATH = os.path.join(REPO_ROOT, "app", "theorem_registry_guard.py")
_SERVER_PATH = os.path.join(REPO_ROOT, "app", "server.py")


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "theorem_registry_guard", _GUARD_PATH)
    assert spec and spec.loader, f"cannot load guard from {_GUARD_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()
RESULT = guard.evaluate(server_path=_SERVER_PATH)


def test_guard_self_test_passes():
    """Trust the checker only after its positive + negative fixtures pass."""
    assert guard._self_test() == 0


def test_server_serves_theorems():
    """The served surface names at least one confirming theorem."""
    assert RESULT["served_count"] >= 1, RESULT
    assert RESULT["registry"], "registry empty — served-name extraction regressed"


def test_no_unbacked_served_theorems():
    """Every served theorem resolves to a real Lean proof declaration."""
    unbacked = RESULT["unbacked"]
    assert not unbacked, (
        "Unbacked served-theorem overclaim(s) — named theorem absent from the "
        "lutar-lean corpus:\n"
        + "\n".join(
            f"  {r['served_as']} (status={r['status']})" for r in unbacked)
    )


def test_lambda_stays_conjecture_one():
    """No served name asserts Λ-uniqueness — it stays Conjecture 1, never proven."""
    assert not RESULT["lambda_violations"], (
        "Λ honesty violation(s):\n"
        + "\n".join(f"  {v}" for v in RESULT["lambda_violations"])
    )


def test_registry_statuses_are_honest():
    """Every entry carries an honest status from the allowed vocabulary."""
    allowed = {"verified", "unbacked"}
    for r in RESULT["registry"]:
        assert r["status"] in allowed, r
        assert isinstance(r["lean_decl_exists"], bool), r
        assert isinstance(r["resolves_as_proof"], bool), r


def test_snapshot_matches_live_corpus_when_present():
    """If a live lutar-lean checkout is available, snapshot verdict matches it.

    Skipped when no live checkout is present (CI runs snapshot-only, offline).
    """
    live = os.environ.get("LUTAR_REPO")
    candidates = [live] if live else []
    candidates += ["/opt/lutar-lean", "/tmp/w3-lutar-lean-corpus"]
    live_repo = next(
        (p for p in candidates if p and os.path.isdir(os.path.join(p, "Lutar"))),
        None,
    )
    if not live_repo:
        return  # honest skip: nothing to compare against
    live_result = guard.evaluate(server_path=_SERVER_PATH, lean_repo=live_repo)
    snap_names = {r["name"]: r["status"] for r in RESULT["registry"]}
    live_names = {r["name"]: r["status"] for r in live_result["registry"]}
    assert snap_names == live_names, (snap_names, live_names)


def test_overall_verdict_ok():
    assert RESULT["ok"], RESULT


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    c = RESULT["counts"]
    print(f"\nregistry: {c['verified']} verified · {c['unbacked']} unbacked")
    sys.exit(1 if failures else 0)
