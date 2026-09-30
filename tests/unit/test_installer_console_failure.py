"""Pin the installer drop-in so console failures stay readable.

sysinstall erases the chosen disk before it writes, so a failure (for example
on NVMe hardware, see projectbluefin/server#308) leaves the target disk
blank. If the unit then halts on failure (upstream default
``FailureAction=halt``) and mutes the console (``--mute-console=yes``), the
operator sees nothing on screen and the journal lives only in RAM, which the
installer then powers off.

These tests pin the drop-in so:

* ``--mute-console=yes`` is gone (the operator sees sysinstall's progress).
* ``--reboot=yes`` is gone (sysinstall itself does not initiate a reboot;
  the unit's ``SuccessAction=reboot`` does, separately from failures).
* ``FailureAction`` is not a halt/poweroff (failures leave the console and
  journal reachable).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DROP_IN = ROOT / "files" / "os" / "systemd" / "system" / "systemd-sysinstall.service.d" / "10-bluefin-installer.conf"


def _sections(text: str) -> dict[str, dict[str, list[str]]]:
    """Parse an INI-style drop-in keeping every value of repeated keys."""
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
            continue
        if current is None:
            continue
        key, _, value = line.partition("=")
        current.setdefault(key, []).append(value)
    return sections


def test_drop_in_exists() -> None:
    assert DROP_IN.is_file(), f"{DROP_IN} must exist"


def test_drop_in_does_not_mute_the_console() -> None:
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    exec_starts = u["Service"]["ExecStart"]
    last = exec_starts[-1]
    assert "--mute-console=yes" not in last, (
        "the operator must see sysinstall's progress and any error on "
        "/dev/console; --mute-console=yes hides them"
    )


def test_drop_in_does_not_pass_reboot_yes() -> None:
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    exec_starts = u["Service"]["ExecStart"]
    last = exec_starts[-1]
    assert "--reboot=yes" not in last, (
        "SuccessAction=reboot handles the reboot on success; --reboot=yes "
        "inside ExecStart makes sysinstall itself reboot, which would skip "
        "the unit's failure path entirely"
    )


def test_drop_in_passes_reboot_no() -> None:
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    exec_starts = u["Service"]["ExecStart"]
    last = exec_starts[-1]
    assert "--reboot=no" in last, (
        f"sysinstall must not trigger the reboot itself; got {last!r}"
    )


def test_drop_in_reboots_on_success() -> None:
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    assert u["Unit"].get("SuccessAction") == ["reboot"], (
        "SuccessAction=reboot keeps the install-and-reboot flow on success"
    )


def test_drop_in_does_not_halt_on_failure() -> None:
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    failure_actions = u["Unit"].get("FailureAction", [])
    assert failure_actions, (
        "FailureAction must be set explicitly; the upstream default "
        "FailureAction=halt hides install failures (see #308)"
    )
    forbidden = {"halt", "poweroff", "kexec"}
    for action in failure_actions:
        assert action not in forbidden, (
            f"FailureAction={action} hides the failure on screen and in the "
            "journal; the operator needs to see what went wrong on the bare "
            "console (the live installer runs from RAM, so the journal must "
            "stay reachable, not be powered off)"
        )


def test_drop_in_still_passes_kernel_and_definitions() -> None:
    text = DROP_IN.read_text(encoding="utf-8")
    assert "${BLUEFIN_INSTALL_KERNEL}" in text, "the disk UKI path must be threaded through"
    assert "--definitions=/run/bluefin/installer/bluefin/repart.d" in text, (
        "the installer's pinned repart.d must still be passed"
    )


def test_drop_in_resets_exec_start_before_redefining() -> None:
    """The drop-in must clear the stock ExecStart= before setting its own;
    without that both run and the install fails."""
    u = _sections(DROP_IN.read_text(encoding="utf-8"))
    exec_starts = u["Service"]["ExecStart"]
    assert exec_starts[0] == "", f"first ExecStart must reset to empty, got {exec_starts[0]!r}"
    assert len(exec_starts) == 2, f"expected ['', '...'], got {exec_starts}"