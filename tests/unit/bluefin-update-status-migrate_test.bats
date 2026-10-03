#!/usr/bin/env bats
#
# Unit tests for files/os/update-check/usr/libexec/bluefin-update-status-migrate.
#
# systemctl is stubbed on PATH: every call appends to LOG and `preset` reads
# SU_PRESET_RESULT (ok/error) to drive the test. The stamp is redirected via
# BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP into BATS_TEST_TMPDIR, so nothing
# touches /var/lib on the host.
#
# The helper exists to catch up nodes that sysupd-into a #366 image
# (projectbluefin/server#367): the preset that enables
# bluefin-update-status.service only applies on first boot. The cases below
# cover the helper's three observable states — enabled, already-stamped, and
# preset failure.

setup() {
    REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
    SCRIPT="${REPO_ROOT}/files/os/update-check/usr/libexec/bluefin-update-status-migrate"
    STUB_DIR="${BATS_TEST_TMPDIR}/bin"
    LOG="${BATS_TEST_TMPDIR}/calls.log"
    STAMP="${BATS_TEST_TMPDIR}/var/lib/bluefin/update-status-migrate.stamp"
    mkdir -p "${STUB_DIR}" "$(dirname "${STAMP}")"
    : > "${LOG}"

    cat > "${STUB_DIR}/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "${LOG}"
case "$*" in
    "preset bluefin-update-status.service")
        case "${SU_PRESET_RESULT:-ok}" in
            ok) exit 0 ;;
            error) exit 1 ;;
        esac
        ;;
    *) exit 0 ;;
esac
EOF
    chmod +x "${STUB_DIR}/systemctl"
}

@test "default-script exists and is executable bash" {
    [ -x "${SCRIPT}" ]
    head -n1 "${SCRIPT}" | grep -qx '#!/usr/bin/bash'
}

@test "calls systemctl preset and stamps on the first run" {
    SU_PRESET_RESULT=ok run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    [ -e "${STAMP}" ]
    [[ "${output}" == *"bluefin-update-status.service preset applied"* ]]
}

@test "ignores a failing systemctl preset and still stamps" {
    SU_PRESET_RESULT=error run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    [ -e "${STAMP}" ]
}

@test "skips systemctl when the stamp already exists" {
    : > "${STAMP}"
    run env PATH="${STUB_DIR}:${PATH}" \
        BLUEFIN_SYSTEMCTL="${STUB_DIR}/systemctl" \
        BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        LOG="${LOG}" \
        bash "${SCRIPT}"
    [ "${status}" -eq 0 ]
    ! grep -q 'systemctl preset' "${LOG}"
}