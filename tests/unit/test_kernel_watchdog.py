"""Contracts for the kernel watchdog patch (issue #377).

FSDK's stock kernel config has no watchdog options at all (grep -i watchdog
files/linux/fdsdk-config.sh returns nothing), so even with systemd's
RuntimeWatchdogSec= enabled `/dev/watchdog` does not exist and any hardware
watchdog driver is missing.  The fix is a new FSDK patch that enables the
watchdog core and the common x86 server / BMC watchdog drivers as modules
so an operator can `modprobe` the one that matches the platform without
rebuilding the kernel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL_PATCH = ROOT / "patches" / "freedesktop-sdk" / "0007-linux-watchdog.patch"
KUBENET_PATCH = ROOT / "patches" / "freedesktop-sdk" / "0006-linux-kubernetes-cilium-networking.patch"
BUILD_MODES_TEST = ROOT / "tests" / "unit" / "test_build_modes.py"


def _added_lines() -> list[str]:
    """Return the lines this patch inserts, with the leading '+' stripped."""
    return [
        line[1:]
        for line in KERNEL_PATCH.read_text().splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def test_patch_targets_fsdk_linux_config_script() -> None:
    text = KERNEL_PATCH.read_text()
    assert "diff --git a/files/linux/fdsdk-config.sh b/files/linux/fdsdk-config.sh" in text
    assert "+++ b/files/linux/fdsdk-config.sh" in text


def test_patch_appends_after_the_existing_tail() -> None:
    """0006 owns the last 3 lines of fdsdk-config.sh; 0007 appends below it."""
    text = KERNEL_PATCH.read_text()
    assert "    enable TRANSPARENT_HUGEPAGE" in text
    assert " fi" in text


@pytest.mark.parametrize(
    "option",
    [
        # Watchdog core must be enabled (not module): /dev/watchdog is the
        # user-facing API for systemd RuntimeWatchdogSec= and friends, and
        # userspace (ipmi-watchdog, the watchdog daemon) opens it on every
        # server image regardless of whether a hardware watchdog is bound.
        "WATCHDOG",
        "WATCHDOG_CORE",
        # Common x86 server watchdog drivers, as modules so they only load
        # on hardware that actually exposes them.
        "I6300ESB_WDT",     # Intel 6300ESB PCI watchdog (older servers).
        "ITCO_WDT",         # Intel ICH/PCH TCO watchdog (most x86 boards).
        "SP5100_TCO",       # AMD SP5100/AM79C974 TCO watchdog (AMD servers).
        "IT87_WDT",         # IT87xx Super-I/O watchdog (older motherboards).
        "NUVOTON_NCT6775_WDT",  # Nuvoton NCT6775 Super-I/O watchdog.
        "SOFT_WATCHDOG",    # Software watchdog: always-available fallback.
        "IPMI_WATCHDOG",    # BMC watchdog (Intel/AMI IPMI 2.0 compliant BMCs).
    ],
)
def test_watchdog_options_are_added(option: str) -> None:
    added = _added_lines()
    assert f"module {option}" in added or f"enable {option}" in added, option


def test_core_options_are_built_in_not_modules() -> None:
    """WATCHDOG and WATCHDOG_CORE must be `enable`d (built-in), not modules.

    Otherwise /dev/watchdog is only present when something has loaded the
    module, which systemd does not do on its own.
    """
    added = _added_lines()
    assert "enable WATCHDOG" in added
    assert "enable WATCHDOG_CORE" in added
    # And conversely, the specific drivers are modules, not built-in: a node
    # that lacks the hardware must not bind a stale watchdog at boot.
    assert "module I6300ESB_WDT" in added
    assert "module ITCO_WDT" in added
    assert "module SP5100_TCO" in added


def test_patch_number_is_one_above_0006() -> None:
    """0007 is the next entry after 0006 in the patch_queue (lexical order)."""
    assert KERNEL_PATCH.name == "0007-linux-watchdog.patch"
    assert int(KERNEL_PATCH.name.split("-", 1)[0]) == int(
        KUBENET_PATCH.name.split("-", 1)[0]
    ) + 1


def test_patches_readme_documents_the_watchdog_patch() -> None:
    """patches/README.md must explain why 0007 exists and when to drop it."""
    readme = (ROOT / "patches" / "README.md").read_text()
    assert "0007-linux-watchdog.patch" in readme


def test_build_modes_recognises_0007_as_full_build_trigger() -> None:
    """A PR that changes patches/freedesktop-sdk/0007-*.patch must trigger a
    full kernel build (image=true), not just `validate`; the 0006 entry in
    test_build_modes.py shows the pattern, and the 0007 entry mirrors it."""
    assert "0007-x.patch" in BUILD_MODES_TEST.read_text()