#!/usr/bin/env python3
"""Emit the key-free BuildStream elements main may upload to the org CAS.

Project #299 ships a follow-up PR that uploads a small set of already-built
elements to ``cache.projectbluefin.io:11002`` from pushes to ``main`` so the
next ``build`` run starts warm. The kernel and ``go.bst`` already go through
``scripts/kernel-cache.sh`` (ghcr.io). This script covers the key-free
elements ``kernel-cache.sh`` does not: the ignition, k0s, kubeadm,
nvidia-container-toolkit and (unsigned) OpenZFS binaries, and the sysext
images that package them.

Safety invariant
----------------

``bst push`` uploads whatever it is given. A push remote configured during
``bst build`` would upload every artifact the build produced, including
``bluefin-server/keys/boot-keys.bst`` (the element whose artifact holds the
release signing keys). This script therefore computes an explicit positive
allow-list and fails closed (non-zero exit) if any of those elements is
key-derived.

An element is *key-derived* and excluded when:

- its path is or ends with ``keys/boot-keys.bst`` (the only element that
  stages private keys directly), or
- one of its local sources references ``files/boot-keys`` at all (the public
  ``files/boot-keys/modules`` subtree is not exempt here: the threat model is
  "an attacker controls the cache", and a controlled public certificate
  still changes the kernel's ``SYSTEM_TRUSTED_KEYS``), or
- it transitively depends (build or runtime, directly or indirectly) on
  any of the above.

Seeds and their transitive dependents are excluded together; a misconfigured
allow-list entry therefore never reaches the wire.

Output
------

Prints one element identifier per line, in the order of ``ALLOWED_ELEMENTS``
by default, or in the order of command-line arguments when any are passed.
Exit code is non-zero if any requested element is key-derived.

Usage
-----

::

    .github/scripts/cache-upload-allowlist.py               # default allow-list
    .github/scripts/cache-upload-allowlist.py ELEM [ELEM]  # explicit subset
    .github/scripts/cache-upload-allowlist.py --check NAME # assert NAME is safe
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ELEMENTS_DIR = REPO_ROOT / "elements"

# Junction declarations are external element libraries whose contents are
# resolved at build time, not parsed here.
JUNCTION_ELEMENTS = {"freedesktop-sdk.bst", "gnome-build-meta.bst"}

# An element is a *key seed* when its path matches the boot-key element or
# one of its local sources names ``files/boot-keys``.
KEY_SEED_SUFFIX = "keys/boot-keys.bst"
KEY_SEED_PATH = "files/boot-keys"

# Explicit positive allow-list of key-free elements to warm the cache with.
# Every entry is asserted key-free by tests/unit/test_cache_upload_allowlist.py
# against the current element graph; a future element that becomes key-derived
# (e.g. adds ``files/boot-keys`` to its sources) will fail that test and
# block the upload.
ALLOWED_ELEMENTS = [
    # Bootstrapping binaries we author and that nothing in their graph uses
    # signing material from ``files/boot-keys``.
    "ignition/ignition.bst",
    "ignition/gptfdisk.bst",
    "k0s/k0s-bin.bst",
    "kubeadm/kubeadm-bin.bst",
    # Unsigned NVIDIA + container-toolkit binaries; the signed variants
    # (nvidia/nvidia-open-595-signed.bst, …) live under a different
    # element and depend on the module key.
    "nvidia/nvidia-open-595.bst",
    "nvidia/nvidia-container-toolkit.bst",
    # OpenZFS userspace, before the ``*signed.bst`` step signs the kernel
    # modules with ``files/boot-keys/linux-module-cert.key``.
    "zfs/openzfs.bst",
    # Sysext images whose only key-derived dependency is filtered out at
    # the explicit allow-list level: oci/zfs-sysext.bst transitively
    # depends on kernel-modules.bst and is therefore *not* in this list.
    "oci/k0s-sysext.bst",
    "oci/kubeadm-sysext.bst",
    "oci/kubestellar-sysext.bst",
    "oci/nvidia-container-toolkit-sysext.bst",
]


def _dep_name(dep):
    """Normalise a build-depends / run-depends / depends entry to an element name."""
    if isinstance(dep, str):
        return dep
    if isinstance(dep, dict):
        filename = dep.get("filename")
        if isinstance(filename, str):
            return filename
    return None


def _local_name(dep_name):
    """Return the local-element name a dependency resolves to, or None.

    Strips any junction-ref prefix (``freedesktop-sdk.bst:components/x.bst``);
    junction targets are external and never key-derived from this graph.
    """
    if not isinstance(dep_name, str):
        return None
    return dep_name.split(":", 1)[0]


def load_elements(elements_dir: Path = ELEMENTS_DIR) -> dict[str, dict]:
    """Return {element_name: {"build": [...], "run": [...], "raw": str}}.

    ``element_name`` is the path relative to ``elements/`` (e.g.
    ``bluefin-server/os-stack.bst``). Junction declarations are skipped.
    """
    elements: dict[str, dict] = {}
    for bst in sorted(elements_dir.rglob("*.bst")):
        name = bst.relative_to(elements_dir).as_posix()
        if bst.name in JUNCTION_ELEMENTS:
            continue
        try:
            doc = yaml.safe_load(bst.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        deps: list[str] = []
        for key in ("build-depends", "depends", "runtime-depends"):
            deps.extend(d for d in (_dep_name(x) for x in (doc.get(key) or [])) if d)
        elements[name] = {
            "build": [d for d in (_dep_name(x) for x in (doc.get("build-depends") or [])) if d],
            "run": [d for d in (_dep_name(x) for x in (doc.get("runtime-depends") or [])) if d],
            "depends": deps,
            "raw": doc,
            "source": bst.read_text(encoding="utf-8"),
        }
    return elements


def key_derived_paths(elements: dict[str, dict]) -> set[str]:
    """Return the set of key-derived element names (seeds + transitive dependents)."""
    seeds = {
        name
        for name, info in elements.items()
        if name.endswith(KEY_SEED_SUFFIX) or KEY_SEED_PATH in info["source"]
    }

    key_derived = set(seeds)
    changed = True
    while changed:
        changed = False
        for name, info in elements.items():
            if name in key_derived:
                continue
            deps = {_local_name(d) for d in info["depends"]}
            if deps & key_derived:
                key_derived.add(name)
                changed = True
    return key_derived


def resolve(elem: str, elements: dict[str, dict]) -> str:
    """Return the local-element name for ``elem`` (strip junction prefix)."""
    local = _local_name(elem)
    if local and local in elements:
        return local
    # Junction reference; the local lookup will not find it. Return the local
    # half and let the caller decide.
    return local or elem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("elements", nargs="*", help="element names to check (default: ALLOWED_ELEMENTS)")
    parser.add_argument(
        "--check",
        metavar="ELEMENT",
        help="assert ELEMENT is key-free; print nothing and exit 0 on success",
    )
    args = parser.parse_args(argv)

    elements = load_elements()
    key_derived = key_derived_paths(elements)

    if args.check is not None:
        local = resolve(args.check, elements)
        if local in key_derived:
            print(f"refusing: {args.check} is key-derived", file=sys.stderr)
            return 1
        return 0

    requested = args.elements or list(ALLOWED_ELEMENTS)
    violations = []
    safe: list[str] = []
    for elem in requested:
        local = resolve(elem, elements)
        if local not in elements and elem not in ALLOWED_ELEMENTS:
            print(f"refusing: {elem} is not a known local element", file=sys.stderr)
            violations.append(elem)
            continue
        if local in key_derived:
            print(f"refusing to upload key-derived element: {elem}", file=sys.stderr)
            violations.append(elem)
            continue
        safe.append(elem)

    if violations:
        return 1

    for elem in safe:
        print(elem)
    return 0


if __name__ == "__main__":
    sys.exit(main())