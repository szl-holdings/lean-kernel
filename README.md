---
title: Lean Kernel — Lutar Invariant Λ
emoji: 🔏
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: true
license: apache-2.0
short_description: Live Lean v4.13.0 kernel for the Lutar Invariant
---

# SZLHOLDINGS/lean-kernel

Live verification kernel for [szl-holdings/lutar-lean](https://github.com/szl-holdings/lutar-lean) — the Lean 4 formalization of the **Lutar Invariant Λ**.

**Λ definition (canonical):** `Λ_k(x) = (∏ xᵢ)^(1/k)` — the unweighted geometric mean of the axis vector (equivalently, the weighted geomean with all Egyptian unit-fraction weights `1/k`). Source of truth: `Lutar/Invariant.lean` and `ouroboros/docs/lambda-spec.md`.

This Space pins **Lean v4.13.0 + Mathlib v4.13.0** (from the repo's `lean-toolchain` + `lakefile.lean`) and exposes a live API + UI.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/lean/healthz` | build status + commit + toolchain |
| GET | `/api/lean/numbers` | canonical numbers, **live** from `lean_numbers.py` against the deployed commit |
| GET | `/api/lean/theorems` | full theorem table — every decl with status PROVEN/AXIOM/SORRY + `file:line` |
| POST | `/api/lean/verify` | verify a Λ-receipt `{"axes":[…],"lambda":…}`; returns Y/N + confirming theorem |
| GET | `/api/lean/build` | trigger `lake build`, stream output via SSE, final pass/fail + timing |
| GET | `/api/lean/vectors` | the 10 golden reference vectors |
| POST | `/api/lean/vectors/exercise` | recompute each vector's Λ against current Lean, pass/fail |

## Honesty

Numbers are **never hardcoded** — every figure is recomputed live from the deployed commit. If `lake build` fails (e.g. Mathlib cache or disk is unavailable on the box), `healthz` and the UI report **BUILD FAIL** with the verbatim error tail. Nothing is faked green.

Doctrine v11 LOCKED. Author: Stephen P. Lutar Jr. (ORCID 0009-0001-0110-4173), SZL Holdings.
