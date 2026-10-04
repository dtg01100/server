#!/usr/bin/env bats
#
# Unit tests for files/os/update-check/usr/libexec/bluefin-update-status-migrate.
#
# systemctl is stubbed on PATH: every call appends to LOG and `preset` reads
# SU_PRESET_RESULT (ok/error) to drive the test. The stamp is redirected via
# BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP into BATS_TEST_TMPDIR, so nothing
# touches /var/lib on the host. The 80-bluefin-updates.preset source is
# pointed at a copy in BATS_TEST_TMPDIR so the helper reads whatever rules
# the test wants it to.
#
# The helper exists to catch up nodes that sysupd-into a #366 image
# (projectbluefin/server#367): the preset that enables
# bluefin-update-status.service only applies on first boot. The cases below
# cover every `enable X` line in 80-bluefin-updates.preset being applied,
# the start-after-preset step, the stamp short-circuit, and preset failure.

setup() {
    REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
    SCRIPT="${REPO_ROOT}/files/os/update-check/usr/libexec/bluefin-update-status-migrate"
    STUB_DIR="${BATS_TEST_TMPDIR}/bin"
    LOG="${BATS_TEST_TMPDIR}/calls.log"
    STAMP="${BATS_TEST_TMPDIR}/var/lib/bluefin/update-status-migrate.stamp"
    PRESET_SRC="${BATS_TEST_TMPDIR}/80-bluefin-updates.preset"
    mkdir -p "${STUB_DIR}" "$(dirname "${STAMP}")" "$(dirname "${PRESET_SRC}")"
    : > "${LOG}"

    cat > "${STUB_DIR}/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "${LOG}"
case "$1 $2" in
    "preset "*)
        case "${SU_PRESET_RESULT:-ok}" in
            ok) exit 0 ;;
            error) exit 1 ;;
        esac
        ;;
    *) exit 0 ;;
esac
EOF
    chmod +x "${STUB_DIR}/systemctl"

    # Mirror the real preset: every unit the helper must `preset` and `start`
    # on the first run.  Tests can overwrite this file before running.
    cat > "${PRESET_SRC}" <<'EOF'
# test preset
enable systemd-sysupdate.timer
enable bluefin-update-status.service
EOF
}

@test "default-script exists and is executable bash" {
    [ -x "${SCRIPT}" ]
    head -n1 "${SCRIPT}" | grep -qx '#!/usr/bin/bash'
}

@test "applies every enable line and stamps on the first run" {
    SU_PRESET_RESULT=ok run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        BLUEFIN_UPDATES_PRESET="${PRESET_SRC}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset systemd-sysupdate.timer' "${LOG}"
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    grep -qx 'systemctl start --no-block systemd-sysupdate.timer' "${LOG}"
    grep -qx 'systemctl start --no-block bluefin-update-status.service' "${LOG}"
    [ -e "${STAMP}" ]
    [[ "${output}" == *"80-bluefin-updates preset applied"* ]]
}

@test "ignores a failing systemctl preset and still stamps" {
    SU_PRESET_RESULT=error run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        BLUEFIN_UPDATES_PRESET="${PRESET_SRC}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    [ -e "${STAMP}" ]
}

@test "the unit gates the script via ConditionPathExists so an existing stamp keeps the script from running on the next boot" {
    # The unit (bluefin-update-status-migrate.service) carries
    # `ConditionPathExists=!/var/lib/bluefin/update-status-migrate.stamp`,
    # so the script is only entered once per node.  The script itself is
    # still idempotent (preset is a no-op when the rule is satisfied),
    # but we don't paper over the gating here.
    unit="$(cat "${REPO_ROOT}/files/os/systemd/system/bluefin-update-status-migrate.service")"
    echo "${unit}" | grep -qx 'ConditionPathExists=!/var/lib/bluefin/update-status-migrate.stamp'
    echo "${unit}" | grep -q '^Before=.*getty\.target'
    ! echo "${unit}" | grep -q 'agetty\.service'
}

@test "ignores comments and disable lines" {
    cat > "${PRESET_SRC}" <<'EOF'
# comment
disable nothing.timer
enable bluefin-update-status.service
EOF
    SU_PRESET_RESULT=ok run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        BLUEFIN_UPDATES_PRESET="${PRESET_SRC}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    ! grep -q 'preset nothing.timer' "${LOG}"
}

@test "does nothing when the preset file is missing" {
    rm -f "${PRESET_SRC}"
    run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        BLUEFIN_UPDATES_PRESET="${PRESET_SRC}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    ! grep -q 'systemctl preset' "${LOG}"
    [ -e "${STAMP}" ]
}