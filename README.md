<!-- szl-investor-header -->
<div align="center">

# lean-kernel

### A live, machine-checked proof kernel that anyone can use to verify SZL's core math claim for themselves.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](LICENSE) [![Doctrine v11](https://img.shields.io/badge/Doctrine-v11_LOCKED-3b82f6?style=flat-square)](https://github.com/szl-holdings/.github/tree/main/doctrine) [![SLSA](https://img.shields.io/badge/SLSA-L1_honest-22c55e?style=flat-square)](https://slsa.dev/spec/v1.0/levels)

[Docs](https://szl-holdings.github.io/docs-site) · [Quickstart](https://szl-holdings.github.io/docs-site/quickstart) · [HF model card →](https://huggingface.co/SZLHOLDINGS/szl-kernels) · [SZL Holdings](https://a-11-oy.com)

</div>

## 💡 Why it matters

Trust in governed AI shouldn't require trusting a vendor's word. This kernel lets you re-run the formal proofs behind SZL's invariant in your browser, so the math is independently checkable, not just asserted.

## ▶️ HF Kernel Suite

> **Note (alignment fix 2026-06-30):** The HF artifact for this repo is the model
> [`SZLHOLDINGS/szl-kernels`](https://huggingface.co/SZLHOLDINGS/szl-kernels) (the unified
> governed-kernel suite). An HF Space `SZLHOLDINGS/lean-kernel` was previously referenced
> here but does not exist — the correct HF entry point is the model card.

**[Open the kernel suite →](https://huggingface.co/SZLHOLDINGS/szl-kernels)**

[![szl-kernels model card](https://raw.githubusercontent.com/szl-holdings/szl-brand/main/kit/logos/png/kanchay-512.png)](https://huggingface.co/SZLHOLDINGS/szl-kernels)

<sub>_SZL Holdings kanchay mark_</sub>

## ⚡ Quick start (30 seconds)

```bash
git clone https://github.com/szl-holdings/lean-kernel.git
cd lean-kernel
make quickstart   # or: see docs.szlholdings.com/quickstart
```

## 🔍 How it works

In two sentences: this component is part of SZL's governed-AI mesh — it enforces policy and emits signed, replayable audit receipts so every AI action can be verified after the fact. The full mathematical foundation, formal proofs, and protocol details are documented below and in the [technical docs](https://szl-holdings.github.io/docs-site).

---

<details>
<summary><strong>📐 Full technical detail, math, and proofs (the proof, not the pitch)</strong></summary>

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

# SZLHOLDINGS/szl-kernels (formerly lean-kernel)

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


</details>

<!-- szl-doctrine-footer -->

---

### Citation & doctrine

Cite this work via [`CITATION.cff`](CITATION.cff). Math foundations: [szl-papers](https://github.com/szl-holdings/szl-papers) · [lutar-lean](https://github.com/szl-holdings/lutar-lean) (kernel `c7c0ba17`).

HF artifact: [`SZLHOLDINGS/szl-kernels`](https://huggingface.co/SZLHOLDINGS/szl-kernels) (model, not Space).

<sub>Λ Conjecture 1 (not a theorem) · 749/14/163 v11 LOCKED (kernel `c7c0ba17`) · SLSA L1 honest · Section 889 = 5 vendors · [SZL Holdings](https://a-11-oy.com) · Apache-2.0 code · CC-BY-4.0 papers</sub>

*Signed-off-by: Stephen Lutar <stephenlutar2@gmail.com>*

---

**Explore the SZL estate:** [a11oy console](https://a-11-oy.com) · [LLM Router](https://github.com/szl-holdings/szl-router) · [Receipt format spec](https://github.com/szl-holdings/governed-receipt-spec) · [Lean proofs](https://github.com/szl-holdings/lutar-lean) · [Docs](https://github.com/szl-holdings/docs-site) · [🤗 SZLHOLDINGS](https://huggingface.co/SZLHOLDINGS)
