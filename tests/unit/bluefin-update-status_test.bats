#!/usr/bin/env bats
#
# Unit tests for files/os/update-check/usr/libexec/bluefin-update-status.
#
# systemctl, journalctl, agetty and systemd-analyze are stubs on PATH. The
# systemctl stub answers for systemd-sysupdate.service from SU_* variables,
# the journalctl stub prints JOURNAL_ERR for -p err (the errors a run logged)
# and nothing otherwise, and the systemd-analyze stub compares versions with
# sort -V.

setup() {
    REPO_ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
    SCRIPT="${REPO_ROOT}/files/os/update-check/usr/libexec/bluefin-update-status"
    STUB_DIR="${BATS_TEST_TMPDIR}/bin"
    LOG="${BATS_TEST_TMPDIR}/calls.log"
    STATE="${BATS_TEST_TMPDIR}/state"
    BOOT="${BATS_TEST_TMPDIR}/boot"
    ISSUE="${BATS_TEST_TMPDIR}/run/issue.d/40-bluefin-update.issue"
    MOTD="${BATS_TEST_TMPDIR}/run/motd"
    mkdir -p "${STUB_DIR}" "${BOOT}/EFI/Linux"
    : > "${LOG}"
    printf 'ID=bluefin-server\nIMAGE_VERSION="1.0"\n' > "${BATS_TEST_TMPDIR}/os-release"
    : > "${BOOT}/EFI/Linux/bluefin-server-1.0.efi"

    cat > "${STUB_DIR}/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "${LOG}"
case "$*" in
    "show -P InvocationID systemd-sysupdate.service") echo "${SU_INVOCATION:-}" ;;
    "show -P ActiveState systemd-sysupdate.service") echo "${SU_ACTIVE:-inactive}" ;;
    "show -P ExecMainExitTimestamp --timestamp=unix systemd-sysupdate.service") echo "${SU_EXIT:-}" ;;
    "show -P Result systemd-sysupdate.service") echo "${SU_RESULT:-success}" ;;
    "is-enabled systemd-sysupdate.timer") echo "${TIMER:-enabled}"; [ "${TIMER:-enabled}" = enabled ] ;;
    *) exit 1 ;;
esac
EOF
    cat > "${STUB_DIR}/journalctl" <<'EOF'
#!/usr/bin/env bash
echo "journalctl $*" >> "${LOG}"
case " $* " in
    *" -p err "*) printf '%s\n' "${JOURNAL_ERR:-}" ;;
    *) ;;
esac
EOF
    cat > "${STUB_DIR}/agetty" <<'EOF'
#!/usr/bin/env bash
echo "agetty $*" >> "${LOG}"
EOF
    cat > "${STUB_DIR}/systemd-analyze" <<'EOF'
#!/usr/bin/env bash
[ "$1" = compare-versions ] && [ "$3" = gt ] || exit 2
[ "$2" != "$4" ] && [ "$(printf '%s\n%s\n' "$2" "$4" | sort -V | tail -n1)" = "$2" ]
EOF
    chmod +x "${STUB_DIR}"/*
}

run_status() {
    run env PATH="${STUB_DIR}:${PATH}" TZ=UTC LOG="${LOG}" \
        BLUEFIN_OS_RELEASE="${BATS_TEST_TMPDIR}/os-release" \
        BLUEFIN_BOOT_PATH="${BOOT}" \
        BLUEFIN_UPDATE_STATE_DIR="${STATE}" \
        BLUEFIN_ISSUE_FILE="${ISSUE}" \
        BLUEFIN_MOTD_FILE="${MOTD}" \
        "$@" bash "${SCRIPT}"
}

@test "a node that never checked shows its version, updates on and never" {
    run_status
    [ "$status" -eq 0 ]
    [ "$(cat "${ISSUE}")" = "Bluefin Server 1.0, automatic updates on
Last update check: never" ]
    [ "$(cat "${MOTD}")" = "$(cat "${ISSUE}")" ]
    grep -qx 'agetty --reload' "${LOG}"
    [ ! -e "${STATE}/state" ]
}

@test "a successful run is recorded and shown as up to date" {
    run_status SU_INVOCATION=aaa SU_EXIT=@1700000000 SU_RESULT=success
    [ "$status" -eq 0 ]
    grep -qx 'last_success=1700000000' "${STATE}/state"
    grep -qx 'recorded=aaa' "${STATE}/state"
    grep -qx 'Last update check: 2023-11-14 22:13 UTC, up to date' "${ISSUE}"
    ! grep -q FAILED "${ISSUE}"
}

@test "a signature failure is a banner line with its message" {
    run_status SU_INVOCATION=bbb SU_EXIT=@1700000600 SU_RESULT=exit-code \
        JOURNAL_ERR=$'Signature verification failed.\nFailed to acquire manifest: Bad message'
    [ "$status" -eq 0 ]
    grep -qx 'Last update check: never' "${ISSUE}"
    grep -qx 'Last update check FAILED 2023-11-14 22:23 UTC: signature verification failed (Signature verification failed.)' "${ISSUE}"
    grep -qx '  journalctl -u systemd-sysupdate.service' "${MOTD}"
    grep -q '_SYSTEMD_INVOCATION_ID=bbb' "${LOG}"
}

@test "an unreachable source is a banner line" {
    run_status SU_INVOCATION=ccc SU_EXIT=@1700000600 SU_RESULT=exit-code \
        JOURNAL_ERR=$'Transfer failed: Couldn\'t connect to server\nFailed to acquire manifest: Input/output error'
    grep -q "FAILED 2023-11-14 22:23 UTC: update source unreachable (Transfer failed: Couldn't connect to server)$" "${ISSUE}"
}

@test "an unclassified failure shows its first error line, or the unit result" {
    run_status SU_INVOCATION=ddd SU_EXIT=@1700000600 SU_RESULT=exit-code JOURNAL_ERR=$'\nDisk full\nNo space left'
    grep -q 'FAILED 2023-11-14 22:23 UTC: Disk full$' "${ISSUE}"
    run_status SU_INVOCATION=eee SU_EXIT=@1700000700 SU_RESULT=timeout JOURNAL_ERR=
    grep -q 'FAILED 2023-11-14 22:25 UTC: systemd-sysupdate.service failed with result timeout$' "${ISSUE}"
}

@test "the error stays until a later check succeeds, and the last success survives it" {
    run_status SU_INVOCATION=a1 SU_EXIT=@1700000000 SU_RESULT=success
    run_status SU_INVOCATION=a2 SU_EXIT=@1700000600 SU_RESULT=exit-code JOURNAL_ERR='HTTP request to x failed with code 404.'
    grep -qx 'Last update check: 2023-11-14 22:13 UTC' "${ISSUE}"
    grep -q 'FAILED 2023-11-14 22:23 UTC: update source unreachable' "${ISSUE}"
    # A refresh at boot (no run since) keeps showing the failure.
    run_status
    grep -q 'FAILED 2023-11-14 22:23 UTC' "${ISSUE}"
    run_status SU_INVOCATION=a3 SU_EXIT=@1700001200 SU_RESULT=success
    grep -qx 'Last update check: 2023-11-14 22:33 UTC, up to date' "${ISSUE}"
    ! grep -q FAILED "${ISSUE}"
}

@test "a run is recorded once" {
    run_status SU_INVOCATION=f1 SU_EXIT=@1700000000 SU_RESULT=success
    run_status SU_INVOCATION=f1 SU_EXIT=@1700000000 SU_RESULT=exit-code JOURNAL_ERR='Transfer failed'
    ! grep -q FAILED "${ISSUE}"
    grep -qx 'last_error=' "${STATE}/state"
}

@test "a run still in progress is not recorded" {
    run_status SU_INVOCATION=g1 SU_ACTIVE=activating SU_EXIT= SU_RESULT=success
    [ ! -e "${STATE}/state" ]
    grep -qx 'Last update check: never' "${ISSUE}"
}

@test "a staged update is shown with its version" {
    : > "${BOOT}/EFI/Linux/bluefin-server-1.1+3.efi"
    run_status SU_INVOCATION=h1 SU_EXIT=@1700000000 SU_RESULT=success
    grep -qx 'Last update check: 2023-11-14 22:13 UTC; 1.1 staged, it boots at the next reboot' "${ISSUE}"
}

@test "an update that used up its boot tries is not shown as staged" {
    : > "${BOOT}/EFI/Linux/bluefin-server-1.1+0-3.efi"
    run_status
    grep -qx 'Last update check: never; 1.1 failed its boot tries, staying on 1.0' "${ISSUE}"
    ! grep -q staged "${ISSUE}"
}

@test "an older UKI kept for rollback is neither staged nor failed" {
    : > "${BOOT}/EFI/Linux/bluefin-server-0.9.efi"
    run_status
    grep -qx 'Last update check: never' "${ISSUE}"
}

@test "disabled automatic updates say so" {
    run_status TIMER=disabled
    grep -qx 'Bluefin Server 1.0, automatic updates off (systemd-sysupdate.timer is disabled)' "${ISSUE}"
}

@test "agetty escapes are neutralised in the issue file only" {
    run_status SU_INVOCATION=k1 SU_EXIT=@1700000000 SU_RESULT=exit-code JOURNAL_ERR='bad path C:\x'
    grep -qF 'bad path C:\\x' "${ISSUE}"
    grep -qF 'bad path C:\x' "${MOTD}"
    ! grep -qF 'C:\\x' "${MOTD}"
}

@test "control characters in an error never reach the banner" {
    run_status SU_INVOCATION=m1 SU_EXIT=@1700000000 SU_RESULT=exit-code \
        JOURNAL_ERR=$'boom\e[2J'
    ! grep -q $'\e' "${ISSUE}"
    grep -q 'FAILED .*: boom\[2J$' "${ISSUE}"
}
