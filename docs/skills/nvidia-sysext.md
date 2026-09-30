---
name: nvidia-sysext
description: Build and ship the NVIDIA driver (open kernel modules) and NVIDIA Container Toolkit (CDI) sysexts for Bluefin Server. Load when adding, bumping, or debugging an NVIDIA flavour, or when wiring the toolkit to a container runtime.
metadata:
  type: how-to
  status: stable
  last_updated: "2026-09-30"
---
# NVIDIA sysexts

Use this skill when working on the NVIDIA driver and toolkit sysexts
shipped as opt-in overlays for Bluefin Server.

## When to Use

- Bumping the pinned version or SHA256 of an NVIDIA driver flavour
  (`include/nvidia.yml`) or the NVIDIA Container Toolkit
  (`include/nvidia-container-toolkit.yml`).
- Adding a new driver flavour (e.g. `nvidia-open-615`).
- Modifying the kernel modules, headless userspace, units, or modprobe /
  sysusers / tmpfiles drop-ins in `files/nvidia/sysext/`.
- Modifying the toolkit contents in `files/nvidia-container-toolkit/sysext/`
  or its activation units.
- Changing the `nvidia-open-<branch>` sysupdate feature / transfer, or the
  `nvidia-container-toolkit` sysupdate transfer.
- Wiring containerd / Kubernetes to the toolkit's CDI spec on a node.

## When NOT to Use

- General sysext loading mechanics (use `systemd-sysext-extensions.md`).
- General sysupdate framing and signing (use `systemd-sysupdate-verification.md`).
- Boot / install / update architecture (use `ddi-installer.md`).
- k0s, kubeadm, kubestellar, or ZFS sysexts (use their own skills).

## Architecture

Bluefin Server's base /usr image includes only what every node needs;
GPU-specific code ships as opt-in `systemd-sysext` overlays. Two extension
images cover the NVIDIA stack:

| Sysext | Image name | Locked to | Purpose |
| --- | --- | --- | --- |
| Driver (open kernel modules) | `nvidia-open-<branch>_<image-version>.raw.zst` | image version | The kernel modules (signed, zstd-compressed) and the headless userspace, GSP firmware and license. |
| Container Toolkit (CDI) | `nvidia-container-toolkit-<ctk-ver>.raw.zst` | own axis (`ID=_any`) | `nvidia-ctk`, `nvidia-cdi-hook` and the upstream `nvidia-cdi-refresh.{service,path}`. Writes `/var/run/cdi/nvidia.yaml` at boot for containerd. |

Both sysexts are opt-in: nothing in the base image enables or ships them,
and `80-bluefin-opt-in.preset` disables the toolkit activation unit. The
recipe and unit layout are shared across all driver flavours; each flavour
adds one row to the flavour table in `include/nvidia.yml` and three
elements (`elements/nvidia/<flavour>.bst`, `-signed.bst`,
`elements/oci/<flavour>-sysext.bst`).

### Open kernel modules only (Turing and newer)

Only the open kernel modules are built — the closed `kernel/` tree is
never compiled. The shared recipe in `include/nvidia-driver.yml` enforces
this: `make -C payload/kernel-open` is the only module build target. The
following modules ship:

```
nvidia nvidia-uvm nvidia-modeset nvidia-drm
```

`nvidia-peermem` is intentionally left out: it requires GPUDirect RDMA
over InfiniBand, which the FSDK kernel does not carry.

The headless userspace (no X driver, no `nvidia-settings`, no `libglvnd`,
no NGX / OptiX / VDPAU / NvFBC / VulkanSC / `nvidia-powerd`) is the
whitelist in `include/nvidia-driver.yml` (`nvidia-libs` and
`nvidia-tools`). The install command refuses to ship anything whose
`DT_NEEDED` it cannot provide from the sysext or `nvidia-external-needed`,
so an NVIDIA library split out in a future release breaks the build
loudly rather than silently linking against a missing symbol.

### Image-locked driver, version-axised toolkit

The driver sysext is image-locked the same way the ZFS sysext is
(`extension-release.nvidia-open-<branch>_<image-version>`,
`ID=bluefin-server`, `VERSION_ID=<image-version>`): the kernel modules
only load on the kernel they were built against. Several driver versions
sit side by side in `/var/lib/extensions` and `systemd-sysext` merges
only the one matching the booted image, so an A/B rollback keeps its
NVIDIA driver.

The toolkit has no kernel ABI: like k0s it uses
`ID=_any` and its own version, merged under the stable name
`nvidia-container-toolkit.raw` from `/var/lib/nvidia-container-toolkit/`.

## Flavour table

`include/nvidia.yml` is the single source of truth for driver flavours.
One comment line separates each flavour's two atoms (`-version`,
`-sha256`) so per-flavour pull requests never edit adjacent lines:

```yaml
variables:
  # nvidia-open-595: production branch 595
  nvidia-open-595-version: "595.104.02"
  nvidia-open-595-sha256: "e421c202e4c79f58c3c7f3161bbe71454ebb3d88936f88205a0e327cd04c59ca"
```

The version is the directory under
`https://download.nvidia.com/XFree86/Linux-x86_64/<version>/`, and the
sha256 is the digest of `NVIDIA-Linux-x86_64-<version>.run`. NVIDIA
publishes `<file>.sha256sum` next to each `.run`; the tracker (see
"Tracking" below) verifies the digest against that file and against the
downloaded bytes.

### Adding a flavour

Adding a driver branch is purely additive — no existing file moves — but
it is not free: each flavour adds roughly 180 MB to the release set.
Pick a branch NVIDIA actually publishes; do not invent one.

1. Add two atoms to `include/nvidia.yml` (new comment line, new
   `<flavour>-version`, new `<flavour>-sha256`).
2. Copy the three element files for the existing flavour and rename:
   - `elements/nvidia/<flavour>.bst`
   - `elements/nvidia/<flavour>-signed.bst`
   - `elements/oci/<flavour>-sysext.bst`
3. Each element must:
   - include `include/nvidia.yml` and `include/nvidia-driver.yml`;
   - set `nvidia-flavour: <flavour>` and
     `nvidia-version: "%{<flavour>-version}"`;
   - reference `bluefin-server/kernel-modules.bst` (the
     `<flavour>-sysext.bst` element only).
4. Add the new flavour to the signed release set in
   `elements/oci/bluefin-server-image.bst` (the list of sysexts to
   stage alongside `bluefin-server_<ver>.raw`).
5. Add the matching sysupdate feature and transfer:
   - `files/os/sysupdate.d/<flavour>.feature` (`[Feature]`,
     `Description=...`; cannot be combined with `zfs`).
   - `files/os/sysupdate.d/<NN>-<flavour>.transfer` — copy
     `33-nvidia-open-595.transfer` and replace the flavour strings.
6. Add `just build-nvidia-sysext` / `export-nvidia-sysext` /
   `dogfood-nvidia` invocations for the new flavour to the
   `[group('sysext')]` recipes in the Justfile.
7. Update `tests/unit/test_nvidia_sysext.py`: the `flavours()` helper
   reads the table from `include/nvidia.yml`, so adding a flavour makes
   every parametrized test run for it automatically.

`tests/unit/test_nvidia_sysext.py` is the merge-contract: every test
runs for every flavour, so a half-done step (no element, no transfer,
no signed-release-set entry) fails there first.

## Repository layout

| Path | Purpose |
| --- | --- |
| `include/nvidia.yml` | Single source of truth for the driver flavour table (atoms only). |
| `include/nvidia-driver.yml` | Shared build / install / sign / stage recipe. |
| `include/nvidia-container-toolkit.yml` | Single source of truth for the toolkit version axis. |
| `elements/nvidia/<flavour>.bst` | Driver build element (unsigned, makeself-extracts the `.run`). |
| `elements/nvidia/<flavour>-signed.bst` | Signs the kernel modules with `linux-module-cert.key`, zstd-compresses them. |
| `elements/oci/<flavour>-sysext.bst` | Stages the sysext EROFS image with `ID=bluefin-server` and `VERSION_ID=<image-version>`. |
| `elements/nvidia/nvidia-container-toolkit.bst` | Toolkit build (Go, vendor modules, no network at build time). |
| `elements/oci/nvidia-container-toolkit-sysext.bst` | Stages the toolkit sysext with `ID=_any`. |
| `elements/bluefin-server/os-nvidia-container-toolkit-sysupdate.bst` | Stages the toolkit sysupdate transfer and feature directory. |
| `files/nvidia/sysext/` | Shared driver units (`nvidia-load.service`, `nvidia-flavour-guard.service`, …), modprobe / sysusers / tmpfiles drop-ins, and the `extension-release.nvidia` template. |
| `files/nvidia-container-toolkit/sysext/` | Toolkit drop-in (`nvidia-cdi-refresh-bluefin.conf`). |
| `files/os/systemd/system/nvidia-container-toolkit-activate.service` | One-shot that copies the staged toolkit image to `/run/extensions/`, refreshes the merge, and starts `nvidia-cdi-refresh.{path,service}`. |
| `files/os/systemd/system/nvidia-container-toolkit-fetch.service` | Fetches the toolkit sysext via sysupdate when nothing is staged. |
| `files/os/sysupdate.d/<flavour>.feature` | Opt-in sysupdate feature for the driver. |
| `files/os/sysupdate.d/<NN>-<flavour>.transfer` | Driver sysupdate transfer (image-locked, `InstancesMax=2`). |
| `files/os/sysupdate.nvidia-container-toolkit.d/71-nvidia-container-toolkit.transfer` | Toolkit sysupdate transfer (own version axis, `CurrentSymlink=`). |
| `files/os/systemd/system-preset/80-bluefin-opt-in.preset` | Disables `nvidia-container-toolkit-activate.service` by default. |
| `Justfile` | `build-nvidia-sysext`, `export-nvidia-sysext`, `dogfood-nvidia`, `build-nvidia-container-toolkit-sysext`, `export-nvidia-container-toolkit-sysext`. |
| `scripts/dogfood-nvidia.sh` | QEMU boot that merges the driver sysext and asserts the GPU-less path skips. |
| `tests/unit/test_nvidia_sysext.py` | Driver-sysext merge contract. |
| `tests/unit/test_nvidia_container_toolkit_sysext.py` | Toolkit-sysext merge contract. |
| `tests/unit/test_nvidia_container_toolkit_delivery.py` | Toolkit activation and sysupdate delivery contract. |
| `.github/scripts/track-binaries.py` | Bumps the toolkit version and the toolkit tarball sha256; `.github/scripts/track-binaries.yml` proposes a PR per component. |

## Per-node selection

A node opts in to the driver by enabling the matching sysupdate feature
on its installed disk, or by dropping the image into `/var/lib/extensions/`
on a diskless node. The toolkit opts in by enabling
`nvidia-container-toolkit-activate.service` (default disabled). Booty's
`extensions` per-node field renders the matching
`/etc/extensions/<flavour>_<ver>.raw` (driver) and adds a profile that
also enables the toolkit activation unit.

A node with no NVIDIA GPU: the driver's
`nvidia-load.service` (and the rest of the GPU-present units) skip
themselves via an `ExecCondition` that walks `/sys/bus/pci/devices/` for
vendor `0x10de` and class `0x03*` (PCI display controllers). The toolkit's
`nvidia-cdi-refresh.{path,service}` are gated upstream by the same
condition; a Bluefin drop-in adds a fallback that skips the refresh path
on a node without an NVIDIA GPU even when the toolkit sysext is merged
(no `nvidia-smi`). Both sysexts can therefore be merged on a node with
no GPU without doing anything.

`nvidia-flavour-guard.service` fails the boot when more than one driver
flavour is merged: the kernel modules ship without a
`modules.dep` index (see `systemd-sysext-extensions.md`), so two flavours
loaded side by side would be ambiguous. The guard reads
`/usr/lib/extension-release.d/extension-release.nvidia-open-*` and
asserts exactly one entry.

## GPU Operator values

The GPU Operator manages its own node labelling and runtime configuration
on top of the toolkit's CDI spec. The two settings the toolkit's CDI
mode expects are:

```yaml
# In nvidia-container-toolkit values.yaml, or equivalent runtime config:
nvidiaDriver:
  enabled: false
nvidiaContainerToolkit:
  enabled: false
cdi:
  enabled: true
```

`nvidia-smi`, CUDA workloads, NVENC/NVDEC, and `dcgm-exporter` all run
through the toolkit's CDI spec at `/var/run/cdi/nvidia.yaml`, which
`nvidia-cdi-refresh.service` regenerates at every path device event and
at boot. containerd (CRI plugin in containerd 2.x, CDI on by default)
reads it automatically; no `nvidia` runtime class or OCI hook is
installed — none of `nvidia-container-runtime`,
`nvidia-container-runtime-hook` or `libnvidia-container` ships.

For time-slicing or MIG partitioning, configure those via
`nvidia-ctk cdi generate` arguments or the GPU Operator's
`gpu-operator-values` (which writes the CDI spec), not by editing the
sysext.

## Build outputs and commands

```bash
just validate                       # resolve the element graph
just build-nvidia-sysext FLAVOUR=nvidia-open-595
just export-nvidia-sysext FLAVOUR=nvidia-open-595
just build-nvidia-container-toolkit-sysext
just export-nvidia-container-toolkit-sysext
just dogfood-nvidia                 # QEMU boot, no GPU; asserts skip, not fail
DOGFOOD_SYSEXT=nvidia scripts/dogfood-install.sh  # full A/B + rollback with both NVIDIA sysexts
```

`just export-image` produces the full signed release set, including
`nvidia-open-595_<ver>.raw.zst` and
`nvidia-container-toolkit-<ctk-ver>.raw.zst` next to
`bluefin-server_<ver>.raw`.

## Tracking

`.github/scripts/track-binaries.py` tracks two NVIDIA components:

- `nvidia-container-toolkit`: patches the toolkit tarball sha256 in
  `elements/nvidia/nvidia-container-toolkit.bst` and bumps the
  `nvidia-container-toolkit-version` atom in
  `include/nvidia-container-toolkit.yml`, on every new release of
  `NVIDIA/nvidia-container-toolkit`.
- `nvidia-open-595`: patches the `.run` sha256 in
  `include/nvidia.yml` to the latest 595.x.x release, by parsing
  the directory listing at
  `https://download.nvidia.com/XFree86/Linux-x86_64/`.

Both verify the sha256 from the checksum file NVIDIA publishes next to
the asset (`<file>.sha256sum`) and against the downloaded bytes, the
same as every other tracked component. A minor bump (moving to a
different NVIDIA branch) is a manual
`apply <component> --version <branch>.<x>.<x>` and a new row in the
flavour table — never an automated PR.

The GPU-present path (`nvidia-smi`, GPU Operator validator, time-slicing,
CUDA, NVENC/NVDEC, `dcgm-exporter`) is not exercised by
`just dogfood-nvidia` (QEMU has no NVIDIA GPU) and is verified on real
hardware during the GPU rollout phase.

## See also

- [systemd-sysext-extensions.md](systemd-sysext-extensions.md) — extension
  loading, kernel-module sysext rules, `bluefin-sysext-modules`.
- [systemd-sysupdate-verification.md](systemd-sysupdate-verification.md) —
  sysupdate transfer / feature syntax, signing, and rollback.
- [k0s-sysext.md](k0s-sysext.md) — sibling sysext, also `ID=_any` on its
  own version axis.
- [booty-integration.md](booty-integration.md) — per-node `extensions`
  field, install profile.
- [skill-improvement.md](skill-improvement.md) — front-matter schema, how
  to update this skill.
- [CONTEXT.md](../../CONTEXT.md) — canonical project domain glossary.
- [projectbluefin/common nvidia skill](https://github.com/projectbluefin/common/blob/main/docs/skills/nvidia.md)
  — desktop / open-distro NVIDIA policy this server-side policy inherits
  from (Turing+, open kernel modules only, same whitelist).
- `systemd-sysext(8)`, `nvidia-ctk(1)`, `systemd-sysupdate(8)`.