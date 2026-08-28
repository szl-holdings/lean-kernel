#!/usr/bin/env python3
"""Fail-closed regression for the kernel image's immutable source inputs.

The test is deliberately standard-library-only so the required build workflow
can run it before downloading project dependencies. It validates the checked-
in Dockerfile/workflow and exercises adversarial fixtures to prove each guard
actually rejects the unsafe or untruthful form it is intended to prevent.

Runs under pytest and directly (``python3 tests/test_dockerfile_source_pins.py``).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "kernel-build-verify.yml"

EXPECTED_UBUNTU_TAG = "26.04"
EXPECTED_UBUNTU_DIGEST = (
    "sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b"
)
EXPECTED_LUTAR_COMMIT = "034ef1cab37c77efec57775e97e99f196def37c8"

_CHECKOUT_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && "
    "github.event.pull_request.head.sha || github.sha }}"
)
_SOURCE_HEAD_GUARD = (
    'test "$(git -C "${LUTAR_REPO}" rev-parse HEAD)" = "${LUTAR_COMMIT}"'
)
_EXPECTED_SOURCE_RUN = " && ".join(
    (
        'RUN git init "${LUTAR_REPO}"',
        'git -C "${LUTAR_REPO}" remote add origin '
        "https://github.com/szl-holdings/lutar-lean.git",
        'git -C "${LUTAR_REPO}" fetch --depth 1 --no-tags origin '
        '"${LUTAR_COMMIT}"',
        'git -C "${LUTAR_REPO}" checkout --detach "${LUTAR_COMMIT}"',
        _SOURCE_HEAD_GUARD,
        'cd "${LUTAR_REPO}"',
        "cat lean-toolchain",
        "lake --version",
    )
)

_EXPECTED_IMAGE_IDENTITY_RUN = "\n".join(
    (
        "docker run --rm \\",
        '  -e EXPECTED_LUTAR_COMMIT="${{ steps.lutar-provenance.outputs.lutar_commit }}" \\',
        '  -e OBSERVED_LUTAR_MAIN_HEAD="${{ steps.lutar-provenance.outputs.lutar_main_head }}" \\',
        "  lean-kernel:ci bash -lc '",
        "    set -euo pipefail",
        '    REPO="${LUTAR_REPO:-/opt/lutar-lean}"',
        '    ACTUAL_LUTAR_COMMIT="$(git -C "$REPO" rev-parse HEAD)"',
        '    test "$LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"',
        '    test "$ACTUAL_LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"',
        '    printf "VERIFIED_PROTECTED_MAIN_ANCESTRY: lutar-lean@%s <= main@%s\\n" \\',
        '      "$ACTUAL_LUTAR_COMMIT" "$OBSERVED_LUTAR_MAIN_HEAD"',
        '    printf "VERIFIED_SOURCE_HEAD: lutar-lean@%s\\n" "$ACTUAL_LUTAR_COMMIT"',
        "  '",
    )
)

_FROM_RE = re.compile(
    r"^FROM[ \t]+ubuntu:(?P<tag>[^@\s]+)"
    r"(?:@(?P<digest>sha256:[0-9a-f]+))?[ \t]*$"
)
_ALL_FROM_RE = re.compile(r"(?mi)^FROM[ \t]+[^\r\n]+$")
_UBUNTU_CLAIM_RE = re.compile(r"(?mi)^#.*\bUbuntu[ \t]+(?P<tag>\d+\.\d+)\b")
_LUTAR_ENV_RE = re.compile(
    r'(?m)^ENV[ \t]+LUTAR_COMMIT="?(?P<commit>[^"\s]+)"?[ \t]*$'
)
_LUTAR_CLAIM_RE = re.compile(
    r"(?mi)^#.*lutar-lean.*protected[ -]main[ \t]+"
    r"(?P<commit>[0-9a-f]{40})\b"
)


def _dockerfile_instructions(dockerfile: str) -> tuple[list[str], list[str]]:
    """Join Dockerfile continuations without interpreting shell syntax."""
    instructions: list[str] = []
    errors: list[str] = []
    parts: list[str] = []

    for line_number, raw_line in enumerate(dockerfile.splitlines(), start=1):
        stripped = raw_line.strip()
        if not parts and (not stripped or stripped.startswith("#")):
            continue
        if not stripped:
            errors.append(
                f"Dockerfile continuation cannot cross blank line {line_number}"
            )
            parts = []
            continue
        continued = stripped.endswith("\\")
        part = stripped[:-1].rstrip() if continued else stripped
        if not part:
            errors.append(f"Dockerfile continuation is empty at line {line_number}")
        parts.append(part)
        if not continued:
            instructions.append(" ".join(parts))
            parts = []

    if parts:
        errors.append("Dockerfile ends with an unterminated continuation")
    return instructions, errors


def _require_exact_line(
    text: str, command: str, error: str, errors: list[str]
) -> None:
    """Require one executable-looking standalone line and reject lookalikes."""
    containing = [line.strip() for line in text.splitlines() if command in line]
    exact = [line for line in containing if line == command]
    if len(exact) != 1 or len(containing) != 1:
        errors.append(error)


def _workflow_run_block(workflow: str, step_name: str) -> str | None:
    """Return one named step's literal run block with base indentation removed."""
    lines = workflow.splitlines()
    marker = f"- name: {step_name}"
    matches = [
        index
        for index, line in enumerate(lines)
        if line.strip() == marker
    ]
    if len(matches) != 1:
        return None

    name_index = matches[0]
    name_indent = len(lines[name_index]) - len(lines[name_index].lstrip())
    run_index: int | None = None
    for index in range(name_index + 1, len(lines)):
        stripped = lines[index].strip()
        indent = len(lines[index]) - len(lines[index].lstrip())
        if stripped.startswith("- name: ") and indent <= name_indent:
            break
        if stripped == "run: |":
            if run_index is not None:
                return None
            run_index = index
    if run_index is None:
        return None

    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    content_indent = run_indent + 2
    body: list[str] = []
    for raw_line in lines[run_index + 1 :]:
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip())
        if stripped and indent <= run_indent:
            break
        if stripped and indent < content_indent:
            return None
        body.append(raw_line[content_indent:].rstrip() if stripped else "")
    while body and not body[-1]:
        body.pop()
    return "\n".join(body)


def pin_contract_errors(dockerfile: str, workflow: str) -> list[str]:
    """Return every immutable-input contract violation found in the files."""
    errors: list[str] = []

    from_lines = [match.group(0).strip() for match in _ALL_FROM_RE.finditer(dockerfile)]
    if len(from_lines) != 1:
        errors.append("Dockerfile must contain exactly one FROM instruction")
        base = None
    else:
        base = _FROM_RE.fullmatch(from_lines[0])
        if base is None:
            errors.append("Dockerfile must use FROM ubuntu:<tag>@sha256:<64-hex-digest>")

    base_tag = base.group("tag") if base is not None else None
    if base is not None:
        base_digest = base.group("digest")
        if base_digest is None:
            errors.append("tag-only FROM is forbidden; the Ubuntu image needs a digest")
        elif re.fullmatch(r"sha256:[0-9a-f]{64}", base_digest) is None:
            errors.append("Ubuntu image digest must be sha256 plus exactly 64 lowercase hex")
        elif base_digest != EXPECTED_UBUNTU_DIGEST:
            errors.append("Ubuntu digest differs from the independently verified OCI index")
        if base_tag != EXPECTED_UBUNTU_TAG:
            errors.append(f"Ubuntu tag must be {EXPECTED_UBUNTU_TAG}")

    ubuntu_claims = list(_UBUNTU_CLAIM_RE.finditer(dockerfile))
    if len(ubuntu_claims) != 1:
        errors.append("Dockerfile must state exactly one Ubuntu release claim")
    elif base_tag is not None and ubuntu_claims[0].group("tag") != base_tag:
        errors.append("Ubuntu comment claim does not match the FROM release")

    sources = list(_LUTAR_ENV_RE.finditer(dockerfile))
    if len(sources) != 1:
        errors.append("Dockerfile must define exactly one LUTAR_COMMIT")
        source_commit = None
    else:
        source_commit = sources[0].group("commit")
        if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
            errors.append("LUTAR_COMMIT must be exactly 40 lowercase hex characters")
        elif source_commit != EXPECTED_LUTAR_COMMIT:
            errors.append("LUTAR_COMMIT differs from the verified protected-main commit")

    source_claims = list(_LUTAR_CLAIM_RE.finditer(dockerfile))
    if len(source_claims) != 1:
        errors.append("Dockerfile must record exactly one protected-main commit claim")
    elif source_commit is not None and source_claims[0].group("commit") != source_commit:
        errors.append("protected-main comment claim does not match LUTAR_COMMIT")

    if re.search(r"git[ \t]+clone\b[^\n]*lutar-lean", dockerfile):
        errors.append("lutar-lean must be fetched by commit, not cloned from a moving ref")

    instructions, instruction_errors = _dockerfile_instructions(dockerfile)
    errors.extend(instruction_errors)
    source_runs = [
        instruction
        for instruction in instructions
        if instruction.startswith("RUN ")
        and "remote add origin " in instruction
        and "lutar-lean.git" in instruction
    ]
    if len(source_runs) != 1:
        errors.append("Dockerfile must contain exactly one canonical lutar-lean source RUN")
    else:
        source_run = source_runs[0]
        segments = source_run.split(" && ")
        exact_fetch = (
            'git -C "${LUTAR_REPO}" fetch --depth 1 --no-tags origin '
            '"${LUTAR_COMMIT}"'
        )
        if exact_fetch not in segments:
            errors.append("Dockerfile must fetch the exact LUTAR_COMMIT without tags")
        if (
            'git -C "${LUTAR_REPO}" checkout --detach "${LUTAR_COMMIT}"'
            not in segments
        ):
            errors.append("Dockerfile must detach checkout at the exact LUTAR_COMMIT")
        if _SOURCE_HEAD_GUARD not in segments:
            errors.append("Dockerfile must verify checkout HEAD equals LUTAR_COMMIT")
        if source_run != _EXPECTED_SOURCE_RUN:
            errors.append(
                "Dockerfile source RUN must remain the exact fail-closed && chain"
            )

    _require_exact_line(
        workflow,
        f"ref: {_CHECKOUT_EXPRESSION}",
        "workflow checkout must bind to the exact pull-request head SHA",
        errors,
    )
    _require_exact_line(
        workflow,
        f"EXPECTED_CHECKOUT_SHA: {_CHECKOUT_EXPRESSION}",
        "workflow must derive its checkout proof from the exact event source SHA",
        errors,
    )
    _require_exact_line(
        workflow,
        'test "$ACTUAL_CHECKOUT_SHA" = "$EXPECTED_CHECKOUT_SHA"',
        "workflow must fail unless checkout HEAD equals the exact event source SHA",
        errors,
    )
    _require_exact_line(
        workflow,
        'CHANGED="$(git diff --name-only --no-renames "$BASE" "$HEAD")"',
        "workflow change detection must fail closed on git diff errors",
        errors,
    )
    _require_exact_line(
        workflow,
        "git -C \"$PROVENANCE_DIR\" remote add origin "
        "https://github.com/szl-holdings/lutar-lean.git",
        "workflow provenance must use the canonical lutar-lean repository",
        errors,
    )
    _require_exact_line(
        workflow,
        'git -C "$PROVENANCE_DIR" fetch --quiet --no-tags --filter=blob:none '
        "origin refs/heads/main:refs/remotes/origin/main",
        "workflow must independently fetch current lutar-lean protected main",
        errors,
    )
    _require_exact_line(
        workflow,
        'git -C "$PROVENANCE_DIR" cat-file -e "$LUTAR_COMMIT^{commit}"',
        "workflow must prove the pinned lutar-lean object is a commit",
        errors,
    )
    _require_exact_line(
        workflow,
        'git -C "$PROVENANCE_DIR" merge-base --is-ancestor "$LUTAR_COMMIT" '
        "refs/remotes/origin/main",
        "workflow must fail unless LUTAR_COMMIT is on current protected main",
        errors,
    )
    _require_exact_line(
        workflow,
        'ACTUAL_LUTAR_COMMIT="$(git -C "$REPO" rev-parse HEAD)"',
        "workflow must read the in-image lutar-lean source HEAD",
        errors,
    )
    _require_exact_line(
        workflow,
        'test "$LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"',
        "workflow must bind the image pin to the independently proven commit",
        errors,
    )
    _require_exact_line(
        workflow,
        'test "$ACTUAL_LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"',
        "workflow must fail unless the in-image source HEAD matches the proven pin",
        errors,
    )
    identity_block = _workflow_run_block(
        workflow, "Verify in-image lutar-lean source identity"
    )
    if identity_block != _EXPECTED_IMAGE_IDENTITY_RUN:
        errors.append(
            "workflow in-image identity proof must remain one canonical fail-closed block"
        )

    return errors


class DockerfileSourcePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def assert_rejected(
        self, dockerfile: str, expected_fragment: str, workflow: str | None = None
    ) -> None:
        errors = pin_contract_errors(dockerfile, workflow or self.workflow)
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"expected error containing {expected_fragment!r}; got {errors!r}",
        )

    def assert_workflow_rejected(self, workflow: str, expected_fragment: str) -> None:
        self.assert_rejected(self.dockerfile, expected_fragment, workflow)

    def test_repository_files_satisfy_pin_contract(self) -> None:
        self.assertEqual([], pin_contract_errors(self.dockerfile, self.workflow))

    def test_rejects_tag_only_from(self) -> None:
        unsafe = self.dockerfile.replace(
            f"ubuntu:{EXPECTED_UBUNTU_TAG}@{EXPECTED_UBUNTU_DIGEST}",
            f"ubuntu:{EXPECTED_UBUNTU_TAG}",
            1,
        )
        self.assert_rejected(unsafe, "tag-only FROM is forbidden")

    def test_rejects_appended_unpinned_final_stage(self) -> None:
        unsafe = f"{self.dockerfile.rstrip()}\nFROM ubuntu:{EXPECTED_UBUNTU_TAG}\n"
        self.assertNotEqual(self.dockerfile, unsafe)
        self.assert_rejected(unsafe, "exactly one FROM instruction")

    def test_rejects_non_40_hex_source_pin(self) -> None:
        unsafe = self.dockerfile.replace(EXPECTED_LUTAR_COMMIT, "main", 1)
        self.assert_rejected(unsafe, "exactly 40 lowercase hex")

    def test_rejects_missing_checkout_verification(self) -> None:
        unsafe = self.dockerfile.replace(
            f"    && {_SOURCE_HEAD_GUARD} \\\n", "", 1
        )
        self.assertNotEqual(self.dockerfile, unsafe, "fixture failed to remove verification")
        self.assert_rejected(unsafe, "verify checkout HEAD")

    def test_rejects_masked_or_nonexecuting_dockerfile_guards(self) -> None:
        original = f"    && {_SOURCE_HEAD_GUARD} \\\n"
        variants = (
            f"    && {_SOURCE_HEAD_GUARD} || true \\\n",
            f"    && {_SOURCE_HEAD_GUARD}; true \\\n",
            f"    && ({_SOURCE_HEAD_GUARD}) || true \\\n",
            f"    && echo '{_SOURCE_HEAD_GUARD}' \\\n",
        )
        for variant in variants:
            with self.subTest(variant=variant.strip()):
                unsafe = self.dockerfile.replace(original, variant, 1)
                self.assertNotEqual(self.dockerfile, unsafe)
                self.assert_rejected(unsafe, "verify checkout HEAD")

    def test_rejects_duplicate_source_pin_or_run(self) -> None:
        env_line = f'ENV LUTAR_COMMIT="{EXPECTED_LUTAR_COMMIT}"'
        second_env = f'ENV LUTAR_COMMIT="{"0" * 40}"'
        duplicate_env = self.dockerfile.replace(
            env_line, f"{env_line}\n{second_env}", 1
        )
        self.assert_rejected(duplicate_env, "exactly one LUTAR_COMMIT")
        duplicate_run = f"{self.dockerfile.rstrip()}\n{_EXPECTED_SOURCE_RUN}\n"
        self.assert_rejected(duplicate_run, "exactly one canonical")

    def test_rejects_source_chain_drift(self) -> None:
        fixtures = (
            (
                "remote add origin https://github.com/szl-holdings/lutar-lean.git",
                "remote add origin https://example.invalid/lutar-lean.git",
            ),
            ("fetch --depth 1 --no-tags origin", "fetch --depth 1 origin"),
            (
                "    && cd \"${LUTAR_REPO}\" \\\n",
                "    && git -C \"${LUTAR_REPO}\" checkout main \\\n"
                "    && cd \"${LUTAR_REPO}\" \\\n",
            ),
        )
        for old, new in fixtures:
            with self.subTest(replacement=new.strip()):
                unsafe = self.dockerfile.replace(old, new, 1)
                self.assertNotEqual(self.dockerfile, unsafe)
                self.assert_rejected(unsafe, "exact fail-closed && chain")

    def test_rejects_false_ubuntu_claim(self) -> None:
        unsafe = self.dockerfile.replace("Ubuntu 26.04", "Ubuntu 24.04", 1)
        self.assert_rejected(unsafe, "Ubuntu comment claim")

    def test_rejects_false_protected_main_claim(self) -> None:
        unsafe = self.dockerfile.replace(
            f"protected main {EXPECTED_LUTAR_COMMIT}",
            f"protected main {'0' * 40}",
            1,
        )
        self.assert_rejected(unsafe, "protected-main comment claim")

    def test_rejects_missing_or_wrong_exact_checkout_ref(self) -> None:
        ref_line = f"ref: {_CHECKOUT_EXPRESSION}"
        replacements = ("", "ref: ${{ github.sha }}")
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                unsafe = self.workflow.replace(ref_line, replacement, 1)
                self.assertNotEqual(self.workflow, unsafe)
                self.assert_workflow_rejected(unsafe, "exact pull-request head SHA")

    def test_rejects_masked_checkout_and_diff_guards(self) -> None:
        checkout_guard = 'test "$ACTUAL_CHECKOUT_SHA" = "$EXPECTED_CHECKOUT_SHA"'
        variants = (
            f"{checkout_guard} || true",
            f"{checkout_guard}; true",
            f"({checkout_guard}) || true",
            f"echo '{checkout_guard}'",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                unsafe = self.workflow.replace(checkout_guard, variant, 1)
                self.assertNotEqual(self.workflow, unsafe)
                self.assert_workflow_rejected(unsafe, "checkout HEAD")
        diff_guard = 'CHANGED="$(git diff --name-only --no-renames "$BASE" "$HEAD")"'
        masked_diff = self.workflow.replace(
            diff_guard,
            'CHANGED="$(git diff --name-only --no-renames "$BASE" "$HEAD" || true)"',
            1,
        )
        self.assert_workflow_rejected(masked_diff, "change detection")

    def test_rejects_masked_or_nonexecuting_ancestry_guards(self) -> None:
        guard = (
            'git -C "$PROVENANCE_DIR" merge-base --is-ancestor '
            '"$LUTAR_COMMIT" refs/remotes/origin/main'
        )
        variants = (f"{guard} || true", f"{guard}; true", f"({guard}) || true", f"echo '{guard}'")
        for variant in variants:
            with self.subTest(variant=variant):
                unsafe = self.workflow.replace(guard, variant, 1)
                self.assertNotEqual(self.workflow, unsafe)
                self.assert_workflow_rejected(unsafe, "current protected main")

    def test_rejects_missing_reversed_or_mutable_ancestry_proof(self) -> None:
        guard = (
            'git -C "$PROVENANCE_DIR" merge-base --is-ancestor '
            '"$LUTAR_COMMIT" refs/remotes/origin/main'
        )
        replacements = (
            "",
            'git -C "$PROVENANCE_DIR" merge-base --is-ancestor '
            'refs/remotes/origin/main "$LUTAR_COMMIT"',
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                unsafe = self.workflow.replace(guard, replacement, 1)
                self.assertNotEqual(self.workflow, unsafe)
                self.assert_workflow_rejected(unsafe, "current protected main")

        canonical_remote = "https://github.com/szl-holdings/lutar-lean.git"
        mutable_remote = "${{ inputs.lutar_repository }}"
        unsafe = self.workflow.replace(canonical_remote, mutable_remote, 1)
        self.assertNotEqual(self.workflow, unsafe)
        self.assert_workflow_rejected(unsafe, "canonical lutar-lean repository")

    def test_rejects_nonexecuting_identity_block_wrapper(self) -> None:
        guard_line = '              test "$ACTUAL_LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"'
        wrapped = (
            "              if false; then\n"
            f"                {guard_line.strip()}\n"
            "              fi"
        )
        unsafe = self.workflow.replace(guard_line, wrapped, 1)
        self.assertNotEqual(self.workflow, unsafe)
        self.assert_workflow_rejected(unsafe, "canonical fail-closed block")

    def test_rejects_missing_workflow_source_attestation(self) -> None:
        unsafe_workflow = self.workflow.replace(
            'test "$ACTUAL_LUTAR_COMMIT" = "$EXPECTED_LUTAR_COMMIT"',
            'echo "$ACTUAL_LUTAR_COMMIT"',
            1,
        )
        self.assert_workflow_rejected(unsafe_workflow, "in-image source HEAD")


if __name__ == "__main__":
    unittest.main()
