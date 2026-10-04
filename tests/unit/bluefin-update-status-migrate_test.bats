#!/usr/bin/env bats
#
# Unit tests for files/os/update-check/usr/libexec/bluefin-update-status-migrate.
#
# systemctl is stubbed: every call is logged, `is-enabled` reads the state
# file ENABLED (present = enabled), and `preset` creates it unless
# PRESET_RESULT says the preset disables the unit or fails. The stamp lives
# in BATS_TEST_TMPDIR.

setup() {
    REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
    SCRIPT="${REPO_ROOT}/files/os/update-check/usr/libexec/bluefin-update-status-migrate"
    STUB="${BATS_TEST_TMPDIR}/systemctl"
    export LOG="${BATS_TEST_TMPDIR}/calls.log"
    export ENABLED="${BATS_TEST_TMPDIR}/enabled"
    STAMP="${BATS_TEST_TMPDIR}/var/lib/bluefin/update-status-migrate.stamp"
    : >"${LOG}"

    cat >"${STUB}" <<'STUB'
#!/usr/bin/env bash
echo "systemctl $*" >>"${LOG}"
case "$1" in
    is-enabled) [ -e "${ENABLED}" ] ;;
    preset)
        case "${PRESET_RESULT:-enable}" in
            enable) : >"${ENABLED}" ;;
            disable) : ;;
            error) exit 1 ;;
        esac
        ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "${STUB}"
}

migrate() {
    run env BLUEFIN_SYSTEMCTL="${STUB}" BLUEFIN_UPDATE_STATUS_MIGRATE_STAMP="${STAMP}" \
        bash "${SCRIPT}"
}

@test "enables and starts bluefin-update-status.service on a node that updated into it" {
    migrate
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    grep -qx 'systemctl start --no-block bluefin-update-status.service' "${LOG}"
    [ -e "${STAMP}" ]
}

@test "touches only bluefin-update-status.service, never the update timers" {
    migrate
    [ "${status}" -eq 0 ]
    ! grep -v 'bluefin-update-status.service$' "${LOG}"
}

@test "leaves an already enabled unit alone" {
    : >"${ENABLED}"
    migrate
    [ "${status}" -eq 0 ]
    ! grep -q 'preset\|start' "${LOG}"
    [ -e "${STAMP}" ]
}

@test "does not start the unit when a preset disables it" {
    PRESET_RESULT=disable migrate
    [ "${status}" -eq 0 ]
    grep -qx 'systemctl preset bluefin-update-status.service' "${LOG}"
    ! grep -q 'start' "${LOG}"
    [ -e "${STAMP}" ]
}

@test "a failing preset does not fail the boot and still stamps" {
    PRESET_RESULT=error migrate
    [ "${status}" -eq 0 ]
    ! grep -q 'start' "${LOG}"
    [ -e "${STAMP}" ]
}
