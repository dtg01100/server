"""Executed coverage for files/kubeadm/sysext/bluefin-kubeadm-containerd-migrate.

The kubeadm sysext ships this helper and runs it from containerd.service
ExecStartPre to backfill the containerd `imports` glob on an installed node
whose /etc/containerd/config.toml predates the glob (review backlog
issue #351: tmpfiles `C` only seeds when absent, so the seeded copy survives
forever). The helper is idempotent: a config that already declares `imports`
is left alone, no mtime change, so DaemonSet / kured watchers stay quiet.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "files" / "kubeadm" / "sysext" / "bluefin-kubeadm-containerd-migrate"

EXPECTED_IMPORTS = (
    'imports = ["/usr/share/bluefin/containerd/conf.d/*.toml", '
    '"/etc/containerd/conf.d/*.toml"]'
)


def _run(config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HELPER), f"--config={config}"],
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )


def _seeded_kubeadm_config() -> str:
    # Mirrors files/kubeadm/sysext/config.toml as it was before `imports`
    # landed: the version the tmpfiles `C` rule still copies on every node
    # that was installed before PR #350. The migrator must add `imports`
    # so drop-ins (NVIDIA Container Toolkit's `nvidia` handler, node
    # overrides in /etc/containerd/conf.d) deep-merge into the kubeadm
    # containerd config.
    return (
        'version = 3\n'
        'root = "/var/lib/containerd"\n'
        'state = "/run/containerd"\n'
        '\n'
        '[grpc]\n'
        '  address = "/run/containerd/containerd.sock"\n'
        '\n'
        '[plugins]\n'
        '  [plugins."io.containerd.cri.v1.images"]\n'
        '    snapshotter = "overlayfs"\n'
        '\n'
        '    [plugins."io.containerd.cri.v1.images".pinned_images]\n'
        '      sandbox = "registry.k8s.io/pause:3.10.1"\n'
        '\n'
        '    [plugins."io.containerd.cri.v1.images".registry]\n'
        '      config_path = "/etc/containerd/certs.d"\n'
        '\n'
        '  [plugins."io.containerd.cri.v1.runtime"]\n'
        '    ignore_image_defined_volumes = true\n'
        '\n'
        '    [plugins."io.containerd.cri.v1.runtime".containerd]\n'
        '      default_runtime_name = "runc"\n'
        '\n'
        '      [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runc]\n'
        '        runtime_type = "io.containerd.runc.v2"\n'
        '\n'
        '        [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runc.options]\n'
        '          BinaryName = "/usr/bin/runc"\n'
        '          SystemdCgroup = true\n'
        '\n'
        '    [plugins."io.containerd.cri.v1.runtime".cni]\n'
        '      bin_dirs = ["/opt/cni/bin", "/usr/libexec/cni"]\n'
        '      conf_dir = "/etc/cni/net.d"\n'
    )


def test_helper_is_bash_that_passes_syntax() -> None:
    assert HELPER.read_text(encoding="utf-8").startswith("#!/usr/bin/bash\n")
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_helper_is_shellcheck_clean_at_warning_level(shellcheck: str) -> None:
    subprocess.run([shellcheck, "-S", "warning", str(HELPER)], check=True)


def test_appends_imports_to_a_pre_existing_config_without_them(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(_seeded_kubeadm_config(), encoding="utf-8")
    original = config.read_text(encoding="utf-8")

    result = _run(config)

    assert result.returncode == 0, result.stderr
    migrated = config.read_text(encoding="utf-8")
    # The original content is preserved as a contiguous block; the migration
    # inserts the imports block immediately before the first `[section]`
    # table header (`[grpc]`), so the file is the seed with the imports
    # block prefixed in place of where `[grpc]` would otherwise be the first
    # post-version content. containerd's TOML loader only consults a
    # top-level `imports`, so a substring check is insufficient — verify
    # the parsed structure carries the imports at the top level and nowhere
    # else.
    assert EXPECTED_IMPORTS in migrated
    # Migrator stamps a blame line so on-disk diffs make it obvious where the
    # line came from without `git log` against /etc/containerd.
    assert "bluefin-kubeadm-containerd-migrate" in migrated
    parsed = tomllib.loads(migrated)
    assert parsed["imports"] == [
        "/usr/share/bluefin/containerd/conf.d/*.toml",
        "/etc/containerd/conf.d/*.toml",
    ]
    # Imports must not be nested under any [plugins.*] table — that placement
    # is what containerd ignores. Walk every nested table (skipping the top
    # level, which legitimately carries `imports`) and assert no `imports`
    # key appears below it.
    def _assert_no_nested_imports(table: object) -> None:
        if isinstance(table, dict):
            for value in table.values():
                if isinstance(value, dict):
                    assert "imports" not in value, (
                        f"imports nested under a subtable is not consulted "
                        f"by containerd: {value!r}"
                    )
                    _assert_no_nested_imports(value)
    _assert_no_nested_imports(parsed)
    # Sanity: the seeded content survives untouched (the migration must
    # not lose any table the kubeadm sysext or operators configured).
    assert 'address = "/run/containerd/containerd.sock"' in migrated
    assert "registry.k8s.io/pause:3.10.1" in migrated
    # The imports block lands before the first [section] table, so the
    # blame comment precedes [grpc] in the file.
    assert migrated.index("# Added by bluefin-kubeadm-containerd-migrate") < migrated.index("[grpc]")
    # Sanity: the original file's content is still all there (none of the
    # seeded lines are dropped or reordered).
    for line in original.strip().splitlines():
        assert line in migrated.splitlines()


def test_idempotent_when_imports_already_present(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(_seeded_kubeadm_config() + "\n" + EXPECTED_IMPORTS + "\n", encoding="utf-8")
    before_mtime = config.stat().st_mtime_ns
    before_inode = config.stat().st_ino
    expected = config.read_text(encoding="utf-8")

    result = _run(config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="utf-8") == expected
    # mtime and inode must not change: DaemonSets and kured watch the file,
    # and a false trigger on every containerd restart would be a regression.
    assert config.stat().st_mtime_ns == before_mtime
    assert config.stat().st_ino == before_inode


def test_existing_config_with_user_overrides_is_preserved(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    user_line = 'sandbox_image = "registry.example.com/pause:3.10.1"'
    config.write_text(_seeded_kubeadm_config().replace(
        'sandbox = "registry.k8s.io/pause:3.10.1"',
        'sandbox = "registry.k8s.io/pause:3.10.1"\n      ' + user_line,
    ), encoding="utf-8")

    result = _run(config)

    assert result.returncode == 0, result.stderr
    migrated = config.read_text(encoding="utf-8")
    assert user_line in migrated, "operator edits survive the migration"
    assert EXPECTED_IMPORTS in migrated


def test_missing_config_is_a_no_op(tmp_path: Path) -> None:
    # tmpfiles has not seeded yet (kubeadm sysext not installed, or the
    # directory is on a read-only fuse snapshot the migrator cannot write).
    # containerd.service's prior ExecStartPre reseeds via tmpfiles; the
    # migrator must not race it or block startup.
    config = tmp_path / "absent.toml"

    result = _run(config)

    assert result.returncode == 0, result.stderr
    assert not config.exists()


def test_top_level_imports_when_config_has_no_section_header(tmp_path: Path) -> None:
    # Some pre-imports kubeadm configs (operator hand-rolls) reach the
    # migrator with only top-level scalars and no [section] table. The
    # migrator must still land `imports` at top level so containerd sees it
    # — appending is correct because there's no `[section]` for it to nest
    # inside.
    config = tmp_path / "config.toml"
    config.write_text('version = 3\nroot = "/var/lib/containerd"\n', encoding="utf-8")

    result = _run(config)

    assert result.returncode == 0, result.stderr
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["imports"] == [
        "/usr/share/bluefin/containerd/conf.d/*.toml",
        "/etc/containerd/conf.d/*.toml",
    ]


def test_imports_with_a_trailing_comment_is_treated_as_present(tmp_path: Path) -> None:
    # Some operators comment the import line they ship. The migrator must
    # not append a second `imports = [...]` and end up with two entries.
    config = tmp_path / "config.toml"
    config.write_text(
        _seeded_kubeadm_config() + '\n# custom drops\nimports = ["/etc/containerd/conf.d/*.toml"]\n',
        encoding="utf-8",
    )

    result = _run(config)

    assert result.returncode == 0, result.stderr
    migrated = config.read_text(encoding="utf-8")
    imports_lines = re.findall(r"^imports\b[^\n]*", migrated, re.MULTILINE)
    assert len(imports_lines) == 1, migrated


def test_unknown_argument_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(_seeded_kubeadm_config(), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(HELPER), "--bogus"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "unknown argument" in result.stderr
    # The config must not be touched on argument errors.
    assert config.read_text(encoding="utf-8") == _seeded_kubeadm_config()


def test_migrator_is_invoked_before_kubelet_modules_load(tmp_path: Path) -> None:
    # The migration writes to /etc/containerd/config.toml, then containerd
    # must load its config (which now imports the drops) before
    # `modprobe br_netfilter` orders kubelet against network plumbing; this
    # ordering is asserted in test_kubeadm_sysext.py, which uses the
    # shipping unit text directly.
    service = (REPO_ROOT / "files" / "kubeadm" / "sysext" / "containerd.service").read_text()
    pre = [line.split("=", 1)[1] for line in service.splitlines() if line.startswith("ExecStartPre=")]
    assert "/usr/libexec/bluefin-kubeadm-containerd-migrate" in pre
    migrate = pre.index("/usr/libexec/bluefin-kubeadm-containerd-migrate")
    assert pre.index("/usr/bin/systemd-tmpfiles --create kubeadm.conf") < migrate
    assert pre.index("/usr/bin/modprobe overlay") > migrate