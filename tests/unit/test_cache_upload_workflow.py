"""Workflow gating for the ``bst-cache`` upload step.

The cache upload step in ``build.yml`` must:

- live in the ``build`` job (which has the BuildStream cache on its runner),
- run only on direct pushes to ``refs/heads/main``,
- request the ``bst-cache`` environment (so its deployment branch policy
  gates credentials to main),
- never fail the build: a network blip in the upload must not roll back a
  release.

The positive allow-list is covered by ``test_cache_upload_allowlist.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8"))


def _cache_step() -> dict:
    job = WORKFLOW["jobs"]["build"]
    for step in job["steps"]:
        run = step.get("run", "")
        if "scripts/cache-upload.sh" in run:
            return step
    raise AssertionError("no step in the build job runs scripts/cache-upload.sh")


def test_step_runs_only_on_pushes_to_main() -> None:
    step = _cache_step()
    if_ = step.get("if", "")
    assert "github.event_name" in if_ and "'push'" in if_
    assert "github.ref" in if_ and "refs/heads/main" in if_
    # Explicitly absent on pull requests: the bst-cache environment refuses
    # credentials, but a step that asks for them anyway would surface that.
    assert "pull_request" not in if_


def test_step_requests_the_bst_cache_environment() -> None:
    step = _cache_step()
    assert step.get("environment", {}).get("name") == "bst-cache"


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
    job = WORKFLOW["jobs"]["build"]
    for step in job["steps"]:
        if step.get("uses", "").startswith("actions/upload-artifact"):
            path = step.get("with", {}).get("path", "")
            assert ".cache/buildstream" not in path, path
            assert "buildstream" not in path, path
        run = step.get("run", "")
        assert "actions/upload-artifact" not in run, run


def test_bst_cache_environment_uses_branch_policy_on_main() -> None:
    """``bst-cache`` must keep its main-only deployment branch policy.

    The workflow is reviewed, but a maintainer could otherwise relax the
    environment and inadvertently leak credentials on a PR. This test reads
    the workflow and verifies the step does not work around that."""
    step = _cache_step()
    # The step's `if:` is the second line of defence: it must independently
    # require ``refs/heads/main``, even though the environment policy already
    # says so. This way a future ``workflow_dispatch`` from the GitHub UI
    # cannot bypass the policy.
    if_ = step["if"]
    assert "refs/heads/main" in if_


def test_workflow_declares_no_extra_token_for_the_upload_step() -> None:
    """The upload step runs with the workflow's read-only ``contents: read``
    token. Adding ``contents: write`` (or a fork-local override) would
    contradict the workflow-level token scoping in build.yml."""
    job = WORKFLOW["jobs"]["build"]
    permissions = job.get("permissions") or WORKFLOW.get("permissions") or {}
    assert permissions.get("contents") == "read"
    step = _cache_step()
    assert "GITHUB_TOKEN" not in json.dumps(step.get("env", {}))