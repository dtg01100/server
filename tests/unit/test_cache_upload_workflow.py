"""Workflow gating for the ``bst-cache`` upload job.

The cache upload in ``build.yml`` lives in its own ``upload-cache`` job
because a step cannot own a GitHub Actions ``environment:`` (steps accept
only ``if/env/run/continue-on-error``). The job runs after ``build`` so the
BuildStream cache lives on its runner, gates to direct pushes on
``refs/heads/main``, requests the ``bst-cache`` environment so its
deployment branch policy scopes the credentials, and never fails the
workflow: a failed upload must never roll back a release.

The positive allow-list is covered by ``test_cache_upload_allowlist.py``;
the script that does the upload by ``test_cache_upload_sh.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))


def _cache_job() -> dict:
    """Return the ``upload-cache`` job, raising if it does not exist."""
    if "upload-cache" not in WORKFLOW["jobs"]:
        raise AssertionError("build.yml has no 'upload-cache' job")
    return WORKFLOW["jobs"]["upload-cache"]


def _cache_step() -> dict:
    job = _cache_job()
    for step in job["steps"]:
        run = step.get("run", "")
        if "scripts/cache-upload.sh" in run:
            return step
    raise AssertionError("no step in the upload-cache job runs scripts/cache-upload.sh")


def test_upload_runs_in_its_own_job() -> None:
    """The upload is a separate job so the ``bst-cache`` environment can
    scope its secrets. A step-level ``environment:`` is invalid (actionlint
    reports `unexpected key \"environment\" for step`) and would have caused
    the workflow to fail to parse."""
    assert "upload-cache" in set(WORKFLOW["jobs"].keys())
    build_steps = WORKFLOW["jobs"]["build"]["steps"]
    for step in build_steps:
        assert "scripts/cache-upload.sh" not in step.get("run", ""), (
            "cache-upload.sh is back inside the build job; a step-level "
            "environment: is not parseable by GitHub Actions."
        )


def test_job_runs_only_after_build_succeeds_and_only_on_pushes_to_main() -> None:
    job = _cache_job()
    if_ = job.get("if", "")
    assert "github.event_name" in if_ and "'push'" in if_
    assert "github.ref" in if_ and "refs/heads/main" in if_
    assert "needs.build.result" in if_ and "'success'" in if_
    # Explicitly absent on pull requests: the bst-cache environment refuses
    # credentials, but a job that asks for them anyway would surface that.
    assert "pull_request" not in if_
    # Upload waits on the build job so the BuildStream artifacts are present.
    needs = job.get("needs", [])
    assert "build" in needs
    assert "changes" in needs


def test_job_requests_the_bst_cache_environment() -> None:
    job = _cache_job()
    assert job.get("environment", {}).get("name") == "bst-cache"


def test_step_skips_cleanly_when_credentials_are_missing() -> None:
    step = _cache_step()
    env = step["env"]
    # vars.CASD_CLIENT_CERT (public) is unconditional.
    assert env["CASD_CLIENT_CERT"] == "${{ vars.CASD_CLIENT_CERT }}"
    # secrets.CASD_CLIENT_KEY is gated to release runs (test_signing_secrets).
    assert env["CASD_CLIENT_KEY"].startswith("${{ needs.changes.outputs.release == 'true' && secrets.")
    # The script itself must exit 0 on missing creds, so the build never
    # blocks on a half-deployed environment.
    script = (ROOT / "scripts" / "cache-upload.sh").read_text(encoding="utf-8")
    assert "skipping cache upload: no credentials" in script


def test_step_never_fails_the_build() -> None:
    """A failed upload must not roll back a release: continue-on-error
    lets the maintainer see the warning without losing the artifact."""
    step = _cache_step()
    assert step.get("continue-on-error") is True


def test_step_does_not_upload_the_buildstream_cache_as_an_artifact() -> None:
    """PR #302 was blocked because the upload step published the entire
    local BuildStream cache (including the boot keys' future artifact)
    as a public Actions artifact. This implementation must not do that:
    the push goes straight to ``bst push`` over mTLS, never through
    actions/upload-artifact."""
    job = _cache_job()
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact"):
            path = step.get("with", {}).get("path", "")
            assert ".cache/buildstream" not in path, path
            assert "buildstream" not in path, path
        run = step.get("run", "")
        assert "actions/upload-artifact" not in run, run


def test_upload_cache_environment_uses_branch_policy_on_main() -> None:
    """``bst-cache`` must keep its main-only deployment branch policy.

    The workflow is reviewed, but a maintainer could otherwise relax the
    environment and inadvertently leak credentials on a PR. This test reads
    the workflow and verifies the job does not work around that."""
    job = _cache_job()
    # The job's `if:` is the second line of defence: it must independently
    # require ``refs/heads/main``, even though the environment policy already
    # says so. This way a future ``workflow_dispatch`` from the GitHub UI
    # cannot bypass the policy.
    if_ = job["if"]
    assert "refs/heads/main" in if_


def test_upload_cache_job_declares_no_extra_token_for_the_upload_step() -> None:
    """The upload job runs with the workflow's read-only ``contents: read``
    token. Adding ``contents: write`` (or a fork-local override) would
    contradict the workflow-level token scoping in build.yml."""
    job = _cache_job()
    permissions = job.get("permissions") or WORKFLOW.get("permissions") or {}
    assert permissions.get("contents") == "read"
    step = _cache_step()
    assert "GITHUB_TOKEN" not in json.dumps(step.get("env", {}))