#!/usr/bin/env python3
"""Served-theorem ↔ real Lean declaration registry guard for lean-kernel.

This is the SERVED-side mirror of lutar-lean's ``check_proven_formulas.py``
(source-side guard, T203) and a11oy's served-formula registry guard (T101).

``app/server.py`` names a confirming Lean theorem for every Λ-receipt it answers
(the ``/api/lean/verify`` surface). This guard proves that every such SERVED /
claimed theorem name resolves to a real Lean ``theorem`` / ``lemma`` / ``def``
DECLARATION in the kernel corpus (``szl-holdings/lutar-lean``). Any served name
that is absent from the corpus is an *unbacked* overclaim: caught here, reported
honestly, and never presented as if it were proven.

Corpus source (honest, reproducible):

  * Default CI / offline mode reads a committed snapshot of the REAL declaration
    names, ``data/lean_corpus_decls.json``, extracted programmatically from the
    lutar-lean tree at a pinned commit (see the snapshot's ``sha`` / ``method``).
    Regenerate it with ``--regenerate --lean-repo <checkout>`` — the names are
    never hand-written, only extracted from real ``.lean`` sources.
  * Live mode (``--lean-repo <checkout>``) indexes the ``.lean`` files directly,
    exactly the corpus the deployed kernel clones to ``/opt/lutar-lean``.

A resolved declaration is EVIDENCE that the named theorem exists in the kernel
sources; it is not a re-run of the Lean kernel and asserts nothing about "the AI
being correct". Λ-uniqueness stays Conjecture 1 (never presented as proven): the
guard fails if any served name smuggles a Λ-uniqueness proof-claim into the set.

Usage::

    python3 app/theorem_registry_guard.py --self-test
    python3 app/theorem_registry_guard.py                 # snapshot mode, human
    python3 app/theorem_registry_guard.py --json          # snapshot mode, JSON
    python3 app/theorem_registry_guard.py --lean-repo /opt/lutar-lean
    python3 app/theorem_registry_guard.py --regenerate --lean-repo ./lutar-lean
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(APP_DIR)
LUTAR_LEAN_URL = "https://github.com/szl-holdings/lutar-lean.git"

# ---------------------------------------------------------------------------
# Served-theorem extraction (from app/server.py — the real served surface)
# ---------------------------------------------------------------------------

# A served Lean theorem reference is a dotted `Lutar.…` path, optionally followed
# by `/name` continuations (e.g. `Lutar.min_le_Λ/Λ_le_max`). Λ and other unicode
# letters are word characters under Python 3 `\w`, so they match here.
SERVED_TOKEN_RE = re.compile(r"Lutar(?:\.[\w']+)+(?:/[\w']+)*")

# A served name that asserts Λ-uniqueness must never enter the confirmed set —
# Λ-uniqueness is Conjecture 1, never presented as proven.
LAMBDA_UNIQUE_TOKENS = ("lambda_unique", "lambda_uniqueness", "λ_unique",
                        "lambdaunique", "conjecture1_lambdaunique")


def _bare(name: str) -> str:
    """Final dotted segment of a (possibly qualified) Lean name."""
    return name.rsplit(".", 1)[-1]


def extract_served_theorems(server_text: str) -> list[dict]:
    """Return the served theorem references named in ``server.py``.

    Each entry: ``{served_as, name, kind_expected}`` where ``served_as`` is the
    qualified string as served and ``name`` is the bare final segment used to
    resolve against the corpus. Duplicates (a name served in several branches)
    are collapsed to one entry.
    """
    seen: dict[str, dict] = {}
    for m in SERVED_TOKEN_RE.finditer(server_text):
        token = m.group(0)
        parts = token.split("/")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            name = _bare(part)
            served_as = part if part.startswith("Lutar.") else f"Lutar.{part}"
            if name not in seen:
                seen[name] = {"served_as": served_as, "name": name,
                              "kind_expected": "proof"}
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------------------
# Lean declaration index (mirrors lutar-lean check_proven_formulas.index_lean_decls)
# ---------------------------------------------------------------------------

DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*"
    r"(?:(?:private|protected|noncomputable|scoped|local)\s+)*"
    r"(?P<kw>theorem|lemma|def)\s+"
    r"(?P<name>[^\s(){}\[\]:⦃⦄⟨⟩]+)"
)
NS_RE = re.compile(r"^\s*namespace\s+(\S+)")
END_RE = re.compile(r"^\s*end\s+(\S+)")


def index_lean_decls(lean_root: str) -> dict:
    """Walk every ``.lean`` file under ``lean_root`` and index decl names."""
    proof_bare: set[str] = set()
    proof_qualified: set[str] = set()
    def_bare: set[str] = set()
    all_bare: set[str] = set()
    all_qualified: set[str] = set()
    files = 0

    for dirpath, dirs, filenames in os.walk(lean_root):
        dirs[:] = [
            d for d in dirs
            if d not in {".git", ".lake", "lake-packages", "build", ".github"}
        ]
        for fn in filenames:
            if not fn.endswith(".lean"):
                continue
            files += 1
            abspath = os.path.join(dirpath, fn)
            try:
                with open(abspath, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            ns_stack: list[str] = []
            for raw in text.splitlines():
                ns_m = NS_RE.match(raw)
                if ns_m:
                    ns_stack.append(ns_m.group(1))
                    continue
                end_m = END_RE.match(raw)
                if end_m and ns_stack:
                    seg = end_m.group(1)
                    if ns_stack[-1].endswith(seg):
                        ns_stack.pop()
                    continue
                m = DECL_RE.match(raw)
                if not m:
                    continue
                name = m.group("name").strip(".")
                if not name:
                    continue
                prefix = ".".join(ns_stack)
                qualified = f"{prefix}.{name}" if prefix else name
                all_bare.add(name)
                all_qualified.add(qualified)
                if m.group("kw") in ("theorem", "lemma"):
                    proof_bare.add(name)
                    proof_qualified.add(qualified)
                else:
                    def_bare.add(name)
    return {
        "proof_bare": proof_bare,
        "proof_qualified": proof_qualified,
        "def_bare": def_bare,
        "all_bare": all_bare,
        "all_qualified": all_qualified,
        "_files": files,
    }


def _resolves(name: str, names_bare: set[str], names_qualified: set[str]) -> bool:
    """True if ``name`` matches a decl, bare / qualified / final-segment."""
    if name in names_bare or name in names_qualified:
        return True
    if "." in name and name.rsplit(".", 1)[-1] in names_bare:
        return True
    return False


def proof_decl_exists(name: str, index: dict) -> bool:
    return _resolves(name, index["proof_bare"], index["proof_qualified"])


def any_decl_exists(name: str, index: dict) -> bool:
    return _resolves(name, index["all_bare"], index["all_qualified"])


# ---------------------------------------------------------------------------
# Corpus snapshot (real extraction, pinned, reproducible)
# ---------------------------------------------------------------------------

SNAPSHOT_SCHEMA = "szl.lean_corpus_decls/v1"
_SET_KEYS = ("proof_bare", "proof_qualified", "def_bare",
             "all_bare", "all_qualified")


def _candidate_snapshot_paths() -> list[str]:
    return [
        os.path.join(APP_DIR, "data", "lean_corpus_decls.json"),   # Docker /opt/app/data
        os.path.join(REPO_ROOT, "data", "lean_corpus_decls.json"),  # repo checkout
    ]


def default_snapshot_path() -> str:
    for p in _candidate_snapshot_paths():
        if os.path.exists(p):
            return p
    return _candidate_snapshot_paths()[-1]


def index_to_snapshot(index: dict, sha: str, ref: str) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "repo": "szl-holdings/lutar-lean",
        "ref": ref,
        "sha": sha,
        "generated_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "method": ("walk every .lean file; index theorem/lemma (proof) and def "
                   "declaration names, bare + namespace-qualified. Names are "
                   "extracted from real sources, never hand-written."),
        "counts": {
            "files": index.get("_files", 0),
            "proof": len(index["proof_bare"]),
            "def": len(index["def_bare"]),
            "all": len(index["all_bare"]),
        },
        "proof_bare": sorted(index["proof_bare"]),
        "proof_qualified": sorted(index["proof_qualified"]),
        "def_bare": sorted(index["def_bare"]),
        "all_bare": sorted(index["all_bare"]),
        "all_qualified": sorted(index["all_qualified"]),
    }


def snapshot_to_index(snapshot: dict) -> dict:
    index = {k: set(snapshot.get(k, [])) for k in _SET_KEYS}
    index["_files"] = snapshot.get("counts", {}).get("files", 0)
    return index


def load_corpus_index(*, lean_repo: str | None,
                      snapshot_path: str | None) -> tuple[dict, dict]:
    """Return ``(index, provenance)`` from a live checkout or the snapshot."""
    if lean_repo:
        index = index_lean_decls(lean_repo)
        prov = {"mode": "live", "lean_repo": lean_repo,
                "sha": _git_sha(lean_repo),
                "files": index.get("_files", 0)}
        return index, prov
    path = snapshot_path or default_snapshot_path()
    with open(path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)
    index = snapshot_to_index(snap)
    prov = {"mode": "snapshot", "snapshot_path": path,
            "repo": snap.get("repo"), "sha": snap.get("sha"),
            "ref": snap.get("ref"), "files": snap.get("counts", {}).get("files")}
    return index, prov


def _git_sha(root: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Registry + verdict
# ---------------------------------------------------------------------------

def _lambda_unique_claim(name: str) -> bool:
    low = name.lower()
    return any(tok in low for tok in LAMBDA_UNIQUE_TOKENS)


def build_registry(served: list[dict], index: dict) -> list[dict]:
    """Honest per-served-name registry: verified | unbacked."""
    registry: list[dict] = []
    for entry in served:
        name = entry["name"]
        is_proof = proof_decl_exists(name, index)
        exists = is_proof or any_decl_exists(name, index)
        if is_proof:
            status = "verified"
        else:
            # Present but only as a def, OR absent entirely: a served theorem
            # that does not resolve to a proof declaration is unbacked.
            status = "unbacked"
        registry.append({
            "served_as": entry["served_as"],
            "name": name,
            "lean_decl_exists": exists,
            "resolves_as_proof": is_proof,
            "status": status,
        })
    return registry


def lambda_violations(served: list[dict]) -> list[str]:
    """A served name asserting Λ-uniqueness stays Conjecture 1, never proven."""
    out: list[str] = []
    for entry in served:
        if _lambda_unique_claim(entry["name"]):
            out.append(
                f"Served name '{entry['served_as']}' asserts Λ-uniqueness; "
                "that stays Conjecture 1 and is never served as proven.")
    return out


def evaluate(*, server_path: str, lean_repo: str | None = None,
             snapshot_path: str | None = None) -> dict:
    with open(server_path, "r", encoding="utf-8") as fh:
        server_text = fh.read()
    served = extract_served_theorems(server_text)
    index, provenance = load_corpus_index(lean_repo=lean_repo,
                                          snapshot_path=snapshot_path)
    registry = build_registry(served, index)
    unbacked = [r for r in registry if r["status"] == "unbacked"]
    lam = lambda_violations(served)
    result = {
        "provenance": provenance,
        "served_count": len(served),
        "registry": registry,
        "counts": {
            "verified": sum(1 for r in registry if r["status"] == "verified"),
            "unbacked": len(unbacked),
        },
        "unbacked": unbacked,
        "lambda_violations": lam,
        "ok": not unbacked and not lam and bool(registry),
    }
    return result


# ---------------------------------------------------------------------------
# Regenerate the committed snapshot from a real lutar-lean checkout
# ---------------------------------------------------------------------------

def regenerate(lean_repo: str, out_path: str, ref: str) -> int:
    if not os.path.isdir(lean_repo):
        print(f"error: --lean-repo {lean_repo} is not a directory",
              file=sys.stderr)
        return 2
    index = index_lean_decls(lean_repo)
    if index.get("_files", 0) == 0:
        print(f"error: no .lean files under {lean_repo}", file=sys.stderr)
        return 2
    snap = index_to_snapshot(index, _git_sha(lean_repo), ref)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    c = snap["counts"]
    print(f"wrote {out_path} @ {snap['sha']} "
          f"({c['files']} files · {c['proof']} proof · {c['def']} def)")
    return 0


# ---------------------------------------------------------------------------
# Self-test (positive + negative fixtures — trust the checker first)
# ---------------------------------------------------------------------------

def _self_test() -> int:
    # Fixture corpus: two real proof decls + one def-only name.
    fake_index = {
        "proof_bare": {"Λ_def", "min_le_Λ"},
        "proof_qualified": {"Lutar.Λ_def", "Lutar.min_le_Λ"},
        "def_bare": {"onlyADef"},
        "all_bare": {"Λ_def", "min_le_Λ", "onlyADef"},
        "all_qualified": {"Lutar.Λ_def", "Lutar.min_le_Λ", "Lutar.onlyADef"},
        "_files": 1,
    }

    # (a) Served name extraction from a server.py-shaped snippet.
    server_snippet = (
        'thm = "Lutar.Invariant.Λ_def (closed-form geomean) + '
        'Lutar.min_le_Λ/Λ_le_max bound"\n'
        '"theorem": "Lutar.a3_normalize_proof (Λ k (const c) = c)"\n'
    )
    served = extract_served_theorems(server_snippet)
    names = {s["name"] for s in served}
    assert names == {"Λ_def", "min_le_Λ", "Λ_le_max", "a3_normalize_proof"}, names

    # (b) A served theorem that resolves to a real proof decl is verified;
    #     one absent from the corpus is caught as unbacked; a def-only match is
    #     also unbacked (a served *theorem* must resolve to a proof decl).
    fixture_served = [
        {"served_as": "Lutar.Λ_def", "name": "Λ_def", "kind_expected": "proof"},
        {"served_as": "Lutar.absent_thm", "name": "absent_thm",
         "kind_expected": "proof"},
        {"served_as": "Lutar.onlyADef", "name": "onlyADef",
         "kind_expected": "proof"},
    ]
    reg = build_registry(fixture_served, fake_index)
    status = {r["name"]: r["status"] for r in reg}
    assert status["Λ_def"] == "verified", status
    assert status["absent_thm"] == "unbacked", status
    assert status["onlyADef"] == "unbacked", status

    # (c) A served name asserting Λ-uniqueness must be caught (stays Conjecture 1).
    lam = lambda_violations(
        [{"served_as": "Lutar.lambda_unique_bad", "name": "lambda_unique_bad"}])
    assert any("Conjecture 1" in v for v in lam), lam
    assert not lambda_violations(fixture_served)

    # (d) Resolution helpers: bare, qualified, final-segment.
    assert _resolves("Λ_def", {"Λ_def"}, set())
    assert _resolves("Lutar.Invariant.Λ_def", set(), set()) is False
    assert _resolves("Lutar.Invariant.Λ_def", {"Λ_def"}, set())

    # (e) Snapshot round-trip is loss-free for the sets the guard needs.
    snap = index_to_snapshot(fake_index, "deadbeef", "main")
    back = snapshot_to_index(snap)
    for k in _SET_KEYS:
        assert back[k] == fake_index[k], (k, back[k], fake_index[k])

    print("self-test OK")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default=os.path.join(APP_DIR, "server.py"),
                    help="path to server.py (the served surface)")
    ap.add_argument("--lean-repo", default=None,
                    help="index a live lutar-lean checkout instead of the snapshot")
    ap.add_argument("--snapshot", default=None,
                    help="path to a corpus snapshot JSON (default: data/lean_corpus_decls.json)")
    ap.add_argument("--json", action="store_true", help="emit JSON verdict")
    ap.add_argument("--self-test", action="store_true",
                    help="run positive + negative fixtures (no repo scan)")
    ap.add_argument("--regenerate", action="store_true",
                    help="rewrite the snapshot from --lean-repo")
    ap.add_argument("--ref", default="main", help="ref label for --regenerate")
    ap.add_argument("--out", default=None,
                    help="output path for --regenerate (default: data/lean_corpus_decls.json)")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.regenerate:
        if not args.lean_repo:
            print("error: --regenerate requires --lean-repo", file=sys.stderr)
            return 2
        out = args.out or _candidate_snapshot_paths()[-1]
        return regenerate(args.lean_repo, out, args.ref)

    result = evaluate(server_path=args.server, lean_repo=args.lean_repo,
                      snapshot_path=args.snapshot)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        p = result["provenance"]
        c = result["counts"]
        print(f"served-theorem registry ({p.get('mode')} corpus "
              f"{p.get('repo', 'lutar-lean')} @ {p.get('sha')}):")
        print(f"  {c['verified']} verified · {c['unbacked']} unbacked "
              f"({result['served_count']} served)")
        for r in result["registry"]:
            mark = {"verified": "OK ", "unbacked": "!! "}[r["status"]]
            print(f"  {mark}{r['served_as']:<32} decl={r['lean_decl_exists']} "
                  f"proof={r['resolves_as_proof']} status={r['status']}")
        for v in result["lambda_violations"]:
            print(f"  Λ VIOLATION: {v}")
        print("VERDICT:", "OK" if result["ok"] else "FAIL")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
