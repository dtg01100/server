"""Safety invariants for the key-free BuildStream artifact allow-list.

The ``bst-cache`` step pushes ``bst push`` elements to the org CAS. These tests
enforce that nothing key-derived is ever uploaded: the boot-key element,
anything embedding files/boot-keys, and any element transitively depending on them,
are excluded, while the named key-free elements are kept in the allow-list
and survive it.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "cache-upload-allowlist.py"

spec = importlib.util.spec_from_file_location("cache_upload_allowlist", SCRIPT)
allowlist_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allowlist_mod)


def _element(name, build=None, run=None, source="", extra_depends=None):
    """A load_elements()-shaped record for a synthetic graph."""
    build = list(build or [])
    run = list(run or [])
    deps = build + run + list(extra_depends or [])
    return {
        "build": build,
        "run": run,
        "depends": deps,
        "raw": {},
        "source": source,
    }


def synthetic_graph() -> dict:
    """A graph with a boot-key seed, a files/boot-keys seed, and dependents."""
    return {
        # The boot-key element itself.
        "bluefin-server/keys/boot-keys.bst": _element("bluefin-server/keys/boot-keys.bst"),
        # Embeds files/boot-keys as a local source.
        "bluefin-server/keys/linux-module-cert.bst": _element(
            "bluefin-server/keys/linux-module-cert.bst",
            source="path: files/boot-keys/modules\n",
        ),
        # Build-depends on the boot-key element.
        "bluefin-server/efi-keys.bst": _element(
            "bluefin-server/efi-keys.bst",
            build=["bluefin-server/keys/boot-keys.bst"],
        ),
        # Transitive runtime dependency on a files/boot-keys source.
        "bluefin-server/boot-init.bst": _element(
            "bluefin-server/boot-init.bst",
            run=["bluefin-server/keys/linux-module-cert.bst"],
        ),
        # Runtime-depends on boot-init (test that the closure works through
        # several hops of transitive runtime deps).
        "bluefin-server/os-stack.bst": _element(
            "bluefin-server/os-stack.bst",
            run=["bluefin-server/boot-init.bst"],
        ),
        # Unrelated, key-free element.
        "ignition/ignition.bst": _element("ignition/ignition.bst"),
    }


def test_key_seeds_and_their_transitive_dependents_are_key_derived() -> None:
    derived = allowlist_mod.key_derived_paths(synthetic_graph())
    assert "bluefin-server/keys/boot-keys.bst" in derived
    assert "bluefin-server/keys/linux-module-cert.bst" in derived
    assert "bluefin-server/efi-keys.bst" in derived
    assert "bluefin-server/boot-init.bst" in derived
    assert "ignition/ignition.bst" not in derived


def test_named_elements_are_in_the_allow_list() -> None:
    for elem in (
        "ignition/ignition.bst",
        "ignition/gptfdisk.bst",
        "k0s/k0s-bin.bst",
        "kubeadm/kubeadm-bin.bst",
        "oci/k0s-sysext.bst",
        "oci/kubeadm-sysext.bst",
    ):
        assert elem in allowlist_mod.ALLOWED_ELEMENTS, f"{elem} missing from ALLOWED_ELEMENTS"


def test_no_allowed_element_is_key_derived_on_the_real_graph() -> None:
    """If any local element were key-derived today, the upload would leak a signing key."""
    elements = allowlist_mod.load_elements()
    derived = allowlist_mod.key_derived_paths(elements)
    for elem in allowlist_mod.ALLOWED_ELEMENTS:
        local = allowlist_mod.resolve(elem, elements)
        assert local not in derived, f"{elem} is key-derived and must not be uploaded"


def test_junction_references_resolve_to_their_local_half() -> None:
    """``freedesktop-sdk.bst:components/foo.bst`` is external, but
    the matching local name (``freedesktop-sdk.bst``) is itself a junction
    declaration we never iterate over. ``local_name`` strips the prefix."""
    assert allowlist_mod._local_name("freedesktop-sdk.bst:components/x.bst") == "freedesktop-sdk.bst"
    assert allowlist_mod._local_name("oci/bluefin-server-image.bst") == "oci/bluefin-server-image.bst"


def test_real_graph_has_a_known_key_derived_seed() -> None:
    """Sanity check that the safety net catches something: the current
    repository always has a boot-key element. If this fails the test
    harness is reading from the wrong checkout."""
    elements = allowlist_mod.load_elements()
    derived = allowlist_mod.key_derived_paths(elements)
    assert "bluefin-server/keys/boot-keys.bst" in derived
    # The kernel-modules element stages files/boot-keys/linux-module-cert.key,
    # so it is also key-derived. Anything that depends on it (osi-image,
    # os-stack, ...) is too.
    assert "bluefin-server/kernel-modules.bst" in derived
    assert "oci/bluefin-server-image.bst" in derived


def test_cli_emits_default_allow_list() -> None:
    result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "ignition/ignition.bst" in result.stdout
    assert "k0s/k0s-bin.bst" in result.stdout


def test_cli_refuses_a_key_derived_element() -> None:
    result = subprocess.run([str(SCRIPT), "oci/bluefin-server-image.bst"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "key-derived" in result.stderr


def test_cli_refuses_an_unknown_local_element() -> None:
    result = subprocess.run([str(SCRIPT), "not/an-element.bst"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "not a known local element" in result.stderr


def test_cli_check_exits_zero_for_key_free() -> None:
    result = subprocess.run([str(SCRIPT), "--check", "ignition/ignition.bst"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_cli_check_exits_nonzero_for_key_derived() -> None:
    result = subprocess.run([str(SCRIPT), "--check", "oci/bluefin-server-image.bst"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "key-derived" in result.stderr


@pytest.mark.parametrize("elem", [
    "oci/bluefin-server-image.bst",
    "oci/bluefin-server-boot.bst",
    "oci/bluefin-server-usr.bst",
    "bluefin-server/keys/boot-keys.bst",
    "bluefin-server/kernel-modules.bst",
    "zfs/openzfs-signed.bst",
    "oci/zfs-sysext.bst",  # depends on kernel-modules and openzfs-signed
])
def test_obviously_key_derived_elements_are_caught(elem: str) -> None:
    elements = allowlist_mod.load_elements()
    derived = allowlist_mod.key_derived_paths(elements)
    assert elem in derived, f"{elem} should be marked key-derived"