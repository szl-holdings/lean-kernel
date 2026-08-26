#!/usr/bin/env python3
"""Fail-closed regression for the kernel image's immutable source inputs.

The test is deliberately standard-library-only so the required build workflow
can run it before downloading project dependencies.  It validates the checked-
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

_FROM_RE = re.compile(
    r"(?m)^FROM[ \t]+ubuntu:(?P<tag>[^@\s]+)"
    r"(?:@(?P<digest>sha256:[0-9a-f]+))?[ \t]*$"
)
_UBUNTU_CLAIM_RE = re.compile(r"(?mi)^#.*\bUbuntu[ \t]+(?P<tag>\d+\.\d+)\b")
_LUTAR_ENV_RE = re.compile(
    r'(?m)^ENV[ \t]+LUTAR_COMMIT="?(?P<commit>[^"\s]+)"?[ \t]*$'
)
_LUTAR_CLAIM_RE = re.compile(
    r"(?mi)^#.*lutar-lean.*protected[ -]main[ \t]+"
    r"(?P<commit>[0-9a-f]{40})\b"
)


def pin_contract_errors(dockerfile: str, workflow: str) -> list[str]:
    """Return every immutable-input contract violation found in the files."""
    errors: list[str] = []

    base = _FROM_RE.search(dockerfile)
    if not base:
        errors.append("Dockerfile must use FROM ubuntu:<tag>@sha256:<64-hex-digest>")
        base_tag = None
        base_digest = None
    else:
        base_tag = base.group("tag")
        base_digest = base.group("digest")
        if base_digest is None:
            errors.append("tag-only FROM is forbidden; the Ubuntu image needs a digest")
        elif re.fullmatch(r"sha256:[0-9a-f]{64}", base_digest) is None:
            errors.append("Ubuntu image digest must be sha256 plus exactly 64 lowercase hex")
        elif base_digest != EXPECTED_UBUNTU_DIGEST:
            errors.append("Ubuntu digest differs from the independently verified OCI index")
        if base_tag != EXPECTED_UBUNTU_TAG:
            errors.append(f"Ubuntu tag must be {EXPECTED_UBUNTU_TAG}")

    ubuntu_claim = _UBUNTU_CLAIM_RE.search(dockerfile)
    if not ubuntu_claim:
        errors.append("Dockerfile must state the Ubuntu release it claims to ship")
    elif base_tag is not None and ubuntu_claim.group("tag") != base_tag:
        errors.append("Ubuntu comment claim does not match the FROM release")

    source = _LUTAR_ENV_RE.search(dockerfile)
    if not source:
        errors.append("Dockerfile must define LUTAR_COMMIT")
        source_commit = None
    else:
        source_commit = source.group("commit")
        if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
            errors.append("LUTAR_COMMIT must be exactly 40 lowercase hex characters")
        elif source_commit != EXPECTED_LUTAR_COMMIT:
            errors.append("LUTAR_COMMIT differs from the verified protected-main commit")

    source_claim = _LUTAR_CLAIM_RE.search(dockerfile)
    if not source_claim:
        errors.append("Dockerfile must record the protected-main commit it claims to pin")
    elif source_commit is not None and source_claim.group("commit") != source_commit:
        errors.append("protected-main comment claim does not match LUTAR_COMMIT")

    if re.search(r"git[ \t]+clone\b[^\n]*lutar-lean", dockerfile):
        errors.append("lutar-lean must be fetched by commit, not cloned from a moving ref")
    if not re.search(
        r'git -C "\$\{LUTAR_REPO\}" fetch --depth 1 --no-tags origin '
        r'"\$\{LUTAR_COMMIT\}"',
        dockerfile,
    ):
        errors.append("Dockerfile must fetch the exact LUTAR_COMMIT without tags")
    if not re.search(
        r'git -C "\$\{LUTAR_REPO\}" checkout --detach "\$\{LUTAR_COMMIT\}"',
        dockerfile,
    ):
        errors.append("Dockerfile must detach checkout at the exact LUTAR_COMMIT")
    if not re.search(
        r'test "\$\(git -C "\$\{LUTAR_REPO\}" rev-parse HEAD\)" '
        r'= "\$\{LUTAR_COMMIT\}"',
        dockerfile,
    ):
        errors.append("Dockerfile must verify checkout HEAD equals LUTAR_COMMIT")

    if 'ACTUAL_LUTAR_COMMIT="$(git -C "$REPO" rev-parse HEAD)"' not in workflow:
        errors.append("workflow must read the in-image lutar-lean source HEAD")
    if 'test "$ACTUAL_LUTAR_COMMIT" = "$LUTAR_COMMIT"' not in workflow:
        errors.append("workflow must fail unless the in-image source HEAD matches the pin")
    if "VERIFIED_SOURCE_HEAD: lutar-lean@$ACTUAL_LUTAR_COMMIT" not in workflow:
        errors.append("workflow must emit an explicit verified source-head attestation")

    return errors


class DockerfileSourcePinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def assert_rejected(self, dockerfile: str, expected_fragment: str) -> None:
        errors = pin_contract_errors(dockerfile, self.workflow)
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"expected error containing {expected_fragment!r}; got {errors!r}",
        )

    def test_repository_files_satisfy_pin_contract(self) -> None:
        self.assertEqual([], pin_contract_errors(self.dockerfile, self.workflow))

    def test_rejects_tag_only_from(self) -> None:
        unsafe = self.dockerfile.replace(
            f"ubuntu:{EXPECTED_UBUNTU_TAG}@{EXPECTED_UBUNTU_DIGEST}",
            f"ubuntu:{EXPECTED_UBUNTU_TAG}",
            1,
        )
        self.assert_rejected(unsafe, "tag-only FROM is forbidden")

    def test_rejects_non_40_hex_source_pin(self) -> None:
        unsafe = self.dockerfile.replace(EXPECTED_LUTAR_COMMIT, "main", 1)
        self.assert_rejected(unsafe, "exactly 40 lowercase hex")

    def test_rejects_missing_checkout_verification(self) -> None:
        unsafe = re.sub(
            r'^\s*&& test "\$\(git -C "\$\{LUTAR_REPO\}" rev-parse HEAD\)" '
            r'= "\$\{LUTAR_COMMIT\}" \\\n',
            "",
            self.dockerfile,
            count=1,
            flags=re.MULTILINE,
        )
        self.assertNotEqual(self.dockerfile, unsafe, "fixture failed to remove verification")
        self.assert_rejected(unsafe, "verify checkout HEAD")

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

    def test_rejects_missing_workflow_source_attestation(self) -> None:
        unsafe_workflow = self.workflow.replace(
            'test "$ACTUAL_LUTAR_COMMIT" = "$LUTAR_COMMIT"',
            'echo "$ACTUAL_LUTAR_COMMIT"',
            1,
        )
        errors = pin_contract_errors(self.dockerfile, unsafe_workflow)
        self.assertIn(
            "workflow must fail unless the in-image source HEAD matches the pin",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
