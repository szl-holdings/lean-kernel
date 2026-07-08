#!/usr/bin/env python3
"""Adversarial fail-closed tests for the Λ-receipt ``/api/lean/verify`` surface.

A well-formed Λ-receipt ``{"axes":[…], "lambda":…}`` must verify exactly as
before. A malformed / ill-typed / out-of-domain receipt must be REJECTED with
HTTP 400 and an honest reason — never crash the service with an unhandled
coercion exception (previously HTTP 500), and never be silently coerced into a
spurious ``Λ=0`` "verified" answer.

These exercise the real request-handling code path: the endpoint is a thin
wrapper over ``server.evaluate_receipt`` / ``server.coerce_axes``, so calling
those directly covers the exact logic the HTTP surface runs — no network, no
extra test dependency.

Runs under pytest and directly (``python3 tests/test_verify_receipt.py``).
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_TEST_DIR)
_SERVER_PATH = os.path.join(REPO_ROOT, "app", "server.py")


def _load_server():
    spec = importlib.util.spec_from_file_location("lean_kernel_server", _SERVER_PATH)
    assert spec and spec.loader, f"cannot load server from {_SERVER_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()


# ---------------------------------------------------------------------------
# Positive: well-formed receipts verify exactly as before (no regression).
# ---------------------------------------------------------------------------

def test_uniform_receipt_verifies():
    payload, status = srv.evaluate_receipt({"axes": [0.9] * 9, "lambda": 0.9})
    assert status == 200, payload
    assert payload["verified"] is True, payload
    assert "a3_normalize_proof" in payload["theorem"], payload


def test_non_uniform_receipt_verifies_with_bound_theorem():
    axes = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    payload, status = srv.evaluate_receipt(
        {"axes": axes, "lambda": 0.4147166274396913})
    assert status == 200, payload
    assert payload["verified"] is True, payload
    assert "min_le_Λ" in payload["theorem"], payload


def test_legitimate_zero_axis_still_verifies():
    """A genuine zero axis pins Λ=0 (reference vector ``one-zero``) — kept honest."""
    axes = [0.9, 0.9, 0.9, 0.9, 0, 0.9, 0.9, 0.9, 0.9]
    payload, status = srv.evaluate_receipt({"axes": axes, "lambda": 0})
    assert status == 200, payload
    assert payload["verified"] is True, payload
    assert "zero-pinning" in payload["theorem"], payload


def test_wrong_claim_is_not_verified():
    payload, status = srv.evaluate_receipt({"axes": [0.9] * 9, "lambda": 0.5})
    assert status == 200, payload
    assert payload["verified"] is False, payload


def test_absent_claim_returns_canonical_recompute():
    payload, status = srv.evaluate_receipt({"axes": [0.9] * 9})
    assert status == 200, payload
    assert payload["verified"] is None, payload
    assert math.isclose(payload["recomputed_lambda"], 0.9, rel_tol=1e-9), payload


def test_dict_axes_are_accepted():
    payload, status = srv.evaluate_receipt(
        {"axes": {"a": 0.5, "b": 0.5}, "lambda": 0.5})
    assert status == 200, payload
    assert payload["verified"] is True, payload


# ---------------------------------------------------------------------------
# Adversarial: malformed / ill-typed / out-of-domain receipts are REJECTED.
# ---------------------------------------------------------------------------

def test_empty_axes_rejected():
    """An empty axis vector is not a valid receipt (previously Λ=0 ⇒ verified)."""
    payload, status = srv.evaluate_receipt({"axes": [], "lambda": 0})
    assert status == 400, payload
    assert payload["verified"] is False, payload
    assert "non-empty" in payload["reason"], payload


def test_negative_axis_rejected():
    """Out-of-domain (x < 0) was silently clamped to Λ=0 and reported verified."""
    payload, status = srv.evaluate_receipt({"axes": [-5.0, -5.0], "lambda": 0})
    assert status == 400, payload
    assert payload["verified"] is False, payload
    assert "non-negative" in payload["reason"], payload


def test_non_finite_axis_rejected():
    for bad in (float("inf"), float("-inf"), float("nan")):
        payload, status = srv.evaluate_receipt({"axes": [bad, 1.0], "lambda": 0})
        assert status == 400, (bad, payload)
        assert payload["verified"] is False, (bad, payload)


def test_non_numeric_axis_rejected_not_crash():
    """A non-numeric axis previously raised an unhandled exception (HTTP 500)."""
    for bad in ("abc", None, [1, 2], {"x": 1}):
        payload, status = srv.evaluate_receipt({"axes": [bad, 1.0], "lambda": 0})
        assert status == 400, (bad, payload)
        assert payload["verified"] is False, (bad, payload)


def test_boolean_axis_rejected_as_ill_typed():
    """JSON ``true``/``false`` are int subclasses in Python but ill-typed axes."""
    payload, status = srv.evaluate_receipt({"axes": [True, True], "lambda": 1})
    assert status == 400, payload
    assert payload["verified"] is False, payload


def test_axes_wrong_container_rejected():
    for bad in (5, "0.9", 0.9, None):
        body = {"axes": bad} if bad is not None else {}
        payload, status = srv.evaluate_receipt(body)
        assert status == 400, (bad, payload)
        assert payload["verified"] is False, (bad, payload)


def test_non_numeric_claim_rejected_not_crash():
    """A non-numeric claimed Λ previously raised an unhandled exception (500)."""
    payload, status = srv.evaluate_receipt({"axes": [0.5, 0.5], "lambda": "abc"})
    assert status == 400, payload
    assert payload["verified"] is False, payload


def test_non_finite_claim_rejected():
    for bad in (float("inf"), float("nan")):
        payload, status = srv.evaluate_receipt({"axes": [0.5, 0.5], "lambda": bad})
        assert status == 400, (bad, payload)
        assert payload["verified"] is False, (bad, payload)


def test_boolean_claim_rejected():
    payload, status = srv.evaluate_receipt({"axes": [1, 1], "lambda": True})
    assert status == 400, payload
    assert payload["verified"] is False, payload


def test_non_object_body_rejected():
    for bad in ("notadict", 5, [1, 2], None):
        payload, status = srv.evaluate_receipt(bad)
        assert status == 400, (bad, payload)
        assert payload["verified"] is False, (bad, payload)


# ---------------------------------------------------------------------------
# Core Λ math: lock canonical values (real coverage of the kernel definition).
# ---------------------------------------------------------------------------

def test_compute_lambda_canonical_values():
    assert srv.compute_lambda([0.5] * 9) == 0.5
    assert srv.compute_lambda([1.0] * 9) == 1.0
    assert srv.compute_lambda([0.9, 0.9, 0.9, 0.9, 0.0, 0.9, 0.9, 0.9, 0.9]) == 0.0
    assert srv.compute_lambda([]) == 0.0
    assert math.isclose(
        srv.compute_lambda([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]),
        0.4147166274396913, rel_tol=1e-12)


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
    print(f"\n{'FAILED' if failures else 'OK'}: "
          f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
