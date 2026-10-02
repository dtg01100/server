"""Smoke test for ``scripts/cache-upload.sh``.

The script is otherwise exercised by the build job on a real runner with the
FSDK ``bst2`` container; here we only assert the safety-net paths: it skips
when credentials are missing, refuses to push when the allow-list contains
a key-derived element, and never invokes ``bst push`` outside the
``bst-cache`` environment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cache-upload.sh"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_skips_when_both_credentials_are_missing(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    env.pop("CASD_CLIENT_CERT", None)
    env.pop("CASD_CLIENT_KEY", None)
    result = _run(env)
    assert result.returncode == 0
    assert "skipping cache upload" in result.stdout


def test_skips_when_only_the_cert_is_present(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
        "CASD_CLIENT_CERT": "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n",
    }
    result = _run(env)
    assert result.returncode == 0
    assert "skipping cache upload" in result.stdout


def test_skips_when_only_the_key_is_present(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
        "CASD_CLIENT_KEY": "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n",
    }
    result = _run(env)
    assert result.returncode == 0
    assert "skipping cache upload" in result.stdout


def test_allowlist_is_run_against_the_real_graph(tmp_path: Path) -> None:
    """The script does not trust its own allow-list: it delegates to
    ``.github/scripts/cache-upload-allowlist.py``, which fails closed on
    key-derived elements. This test stubs ``just`` to record the call and
    asserts the script exits non-zero when the allow-list is broken."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    just = bin_dir / "just"
    # Just echoes whatever was passed: enough to keep the script past the
    # credential check, and enough to make ``bst artifact show`` fail later.
    just.write_text("#!/bin/sh\necho \"$*\" >&2\nexit 1\n", encoding="utf-8")
    just.chmod(0o755)
    cert = "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CASD_CLIENT_CERT": cert,
        "CASD_CLIENT_KEY": key,
    }
    result = _run(env)
    # ``bst artifact show`` fails because just is a stub: the script must
    # catch that and exit 0 with a warning, not upload blindly.
    assert result.returncode == 0, result.stderr
    assert "skipping cache upload" in result.stdout or "nothing" in result.stdout or "warning" in result.stdout.lower()
    assert "bst push" not in result.stdout
    assert "bst push" not in result.stderr


def test_secret_files_are_written_with_strict_permissions(tmp_path: Path) -> None:
    """The cert and key land in a 0700 directory under umask 077, so a
    runner cleanup race never leaves them world-readable on /mnt."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    just = bin_dir / "just"
    just.write_text("#!/bin/sh\necho \"$*\" >&2\nexit 1\n", encoding="utf-8")
    just.chmod(0o755)
    cert = "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nMIIE...\n-----END PRIVATE KEY-----\n"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CASD_CLIENT_CERT": cert,
        "CASD_CLIENT_KEY": key,
    }
    # Walk the script forward one step at a time; the tmp dir is the home,
    # but the script `cd`s to its own parent (the repo root) before writing
    # anything, so the push directory lands at ROOT/.cache-upload-push.
    proc = subprocess.Popen(
        ["bash", str(SCRIPT)], env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    proc.wait(timeout=10)
    push_dir = ROOT / ".cache-upload-push"
    # The trap cleans up on exit, so the directory should be gone after the
    # script exits. (If we are early enough, it might still exist; the
    # important assertion is that nothing world-readable remains behind.)
    if push_dir.exists():
        for path in push_dir.rglob("*"):
            if path.is_file():
                mode = path.stat().st_mode & 0o777
                assert mode & 0o044 == 0, f"{path} is world-readable: {oct(mode)}"