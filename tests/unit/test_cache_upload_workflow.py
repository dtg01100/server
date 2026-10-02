"""Workflow gating for the ``bst-cache`` upload step.

The cache upload in ``build.yml`` lives as a step at the tail of the
``build`` job, gated by ``needs.changes.outputs.release == 'true'``. That
puts the upload on the same runner as the kernel build, so the
``~/.cache/buildstream`` directory the kernel build wrote into is the
cache the upload step pushes -- a separate runner would have a fresh,
empty cache. The ``release`` output is only set on direct pushes to
``main`` (see the ``changes`` job), so PRs, the nightly schedule, and
dispatches never invoke the step. A failed upload never rolls back a
release (``continue-on-error: true``).

The positive allow-list is covered by ``test_cache_upload_allowlist.py``;
the script that does the upload by ``test_cache_upload_sh.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))


def _upload_step() -> dict:
    """Return the cache-upload step inside the ``build`` job."""
    assert "build" in WORKFLOW["jobs"], "build.yml has no 'build' job"
    for step in WORKFLOW["jobs"]["build"]["steps"]:
        run = step.get("run", "")
        if "scripts/cache-upload.sh" in run:
            return step
    raise AssertionError(
        "no step in the build job runs scripts/cache-upload.sh; "
        "the upload must live on the same runner as the kernel build."
    )


def test_upload_lives_at_the_tail_of_the_build_job() -> None:
    """The upload step must run inside the ``build`` job, not a new
    ``upload-cache`` job. A separate runner would have no
    ``~/.cache/buildstream`` artifacts to push: the kernel build writes
    there, and the upload step reads from there."""
    assert "upload-cache" not in WORKFLOW["jobs"], (
        "build.yml has an upload-cache job; a fresh runner would have an "
        "empty BuildStream cache and bst artifact push would have nothing "
        "to upload."
    )
    # The upload step is the last one in build (its comment in build.yml
    # explains the placement).
    steps = WORKFLOW["jobs"]["build"]["steps"]
    assert "scripts/cache-upload.sh" in steps[-1].get("run", "")


def test_upload_step_is_gated_by_changes_outputs_release() -> None:
    """``needs.changes.outputs.release == 'true'`` is the second line of
    defence after the bst-cache environment. The ``changes`` job sets
    ``release`` only on direct pushes to ``main`` (push / workflow_dispatch
    with GITHUB_REF=refs/heads/main); PRs, the schedule, and branch
    dispatches reach the build job with ``release=false`` and skip this
    step entirely. Belt and braces: even if a future maintainer relaxes
    the bst-cache environment's deployment branch policy, this ``if:``
    keeps credentials off every other event."""
    step = _upload_step()
    if_ = step.get("if", "")
    assert "needs.changes.outputs.release == 'true'" in if_, if_


def test_step_runs_only_after_the_kernel_build_finishes() -> None:
    """The upload step is the last in the build job, after
    ``just export-image``, so the BuildStream artifacts the upload pushes
    are the artifacts this build actually produced."""
    steps = WORKFLOW["jobs"]["build"]["steps"]
    last = steps[-1]
    assert "scripts/cache-upload.sh" in last.get("run", "")


def test_step_uses_release_only_credentials() -> None:
    """``secrets.CASD_CLIENT_KEY`` must not appear on any branch but main.
    The release output is the gate; this test pins the gate at the
    workflow level so it cannot be relaxed without breaking this test."""
    step = _upload_step()
    env = step["env"]
    # vars.CASD_CLIENT_CERT is the public half of the mTLS pair (it's a
    # public certificate). It is unconditional on the step; the script
    # refuses to push if the matching key is absent.
    assert env["CASD_CLIENT_CERT"] == "${{ vars.CASD_CLIENT_CERT }}"
    # secrets.CASD_CLIENT_KEY is wrapped in the same release-output gate
    # the rest of build.yml uses, so test_signing_secrets_and_publishing_
    # are_release_only sees it as release-scoped.
    assert env["CASD_CLIENT_KEY"].startswith(
        "${{ needs.changes.outputs.release == 'true' && secrets."
    )
    # The script itself must exit 0 on missing creds, so the build never
    # blocks on a half-deployed environment.
    script = (ROOT / "scripts" / "cache-upload.sh").read_text(encoding="utf-8")
    assert "skipping cache upload: no credentials" in script


def test_step_never_fails_the_build() -> None:
    """A failed upload must not roll back a release: ``continue-on-error``
    lets the maintainer see the warning without losing the artifact."""
    step = _upload_step()
    assert step.get("continue-on-error") is True


def test_step_does_not_upload_the_buildstream_cache_as_an_artifact() -> None:
    """PR #302 was blocked because the upload step published the entire
    local BuildStream cache (including the boot keys' future artifact)
    as a public Actions artifact. This implementation must not do that:
    the push goes straight to ``bst artifact push`` over mTLS, never
    through ``actions/upload-artifact``."""
    for step in WORKFLOW["jobs"]["build"]["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact"):
            path = step.get("with", {}).get("path", "")
            assert ".cache/buildstream" not in path, path
            assert "buildstream" not in path, path
        run = step.get("run", "")
        assert "actions/upload-artifact" not in run, run


def test_workflow_does_not_grant_contents_write_to_the_build_job() -> None:
    """``build`` keeps its read-only token. ``contents: write`` is granted
    only to ``release``, which is gated to ``main``; ``build`` uploads to
    the org CAS through the mTLS credentials in the bst-cache environment,
    not the GITHUB_TOKEN."""
    build_job = WORKFLOW["jobs"]["build"]
    permissions = build_job.get("permissions") or WORKFLOW.get("permissions") or {}
    assert permissions.get("contents") == "read"
    step = _upload_step()
    assert "GITHUB_TOKEN" not in json.dumps(step.get("env", {}))