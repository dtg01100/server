kind: import
description: |
  Install the systemd units from files/os/systemd/system: the first-boot k0s
  sysext activation units, first-start SSH host key generation
  (ssh-host-keygen.service plus its sshd.service and systemd-ssh-generator
  sshd-generated@.service drop-ins), the installer
  source ESP mounts (run-bluefin-boot.mount for netboot,
  run-bluefin-installer.mount for the USB installer), and the
  systemd-sysinstall.service drop-in the USB installer boots into, the update
  units (systemd-sysupdate(-reboot) drop-ins for diskless conditions, the
  kured flag, the reboot interlock: operator lock files, failed-update guard,
  Kubernetes; the boot deadline; the diskless update check;
  bluefin-update-status.service for the login banners), and the
  one-shot update-preset migration unit (bluefin-update-status-migrate.service,
  re-applies 80-bluefin-updates.preset on nodes that sysupd-into an image
  where the preset added a new rule; auto-enabled via multi-user.target.wants/
  in elements/oci/bluefin-server-usr.bst — projectbluefin/server#367).

sources:
  - kind: local
    path: files/os/systemd/system

config:
  target: /usr/lib/systemd/system
