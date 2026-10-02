#!/usr/bin/env bash
# Upload the key-free BuildStream artifacts main may share, and that no
# cache already serves, to the project's artifact cache.
#
#   CASD_CLIENT_CERT=<pem> CASD_CLIENT_KEY=<pem> cache-upload.sh
#
# .github/workflows/build.yml runs this once after `build` succeeds on a push
# to main, behind the `bst-cache` environment that gates credentials to that
# branch. The rules are in "Build time and caches" in
# docs/skills/ci-tooling.md:
#
#  - The push configuration exists only while this script runs and is only
#    passed to `bst artifact push` below. It is never active during
#    `bst build`, which would upload every artifact it builds, including
#    bluefin-server/keys/boot-keys.bst.
#  - Only an explicit element list is pushed (--deps none), computed by
#    .github/scripts/cache-upload-allowlist.py: no key-bearing element and
#    nothing that depends on one.
#  - Of those, only the artifacts cached locally that no remote serves yet
#    are pushed. A fresh CI runner pulls every artifact a remote has, so this
#    is exactly what the build compiled. It is asked of the remotes
#    themselves, by `bst artifact show` with an empty cache directory,
#    rather than read off the build log: that holds on a warm cache too, and
#    does not depend on log wording.
#
# BST_CACHE_PUSH_URL / BST_CACHE_PULL_URL override the cache endpoints for a
# local rehearsal against a throwaway cache server; a plain http:// push URL
# gets no client certificate, which BuildStream refuses on insecure channels.
#
# Missing credentials: this script exits 0 with a notice, so the build passes
# on a branch whose deploys are not yet configured.
set -euo pipefail

cd "$(dirname "$0")/.."

push_url="${BST_CACHE_PUSH_URL:-https://cache.projectbluefin.io:11002}"
pull_url="${BST_CACHE_PULL_URL:-https://cache.projectbluefin.io:11001}"

if [ -z "${CASD_CLIENT_CERT:-}" ] || [ -z "${CASD_CLIENT_KEY:-}" ]; then
    echo "skipping cache upload: no credentials"
    exit 0
fi

elements="$(python3 .github/scripts/cache-upload-allowlist.py)"
if [ -z "${elements}" ]; then
    echo "cache-upload: allow-list is empty; nothing to upload."
    exit 0
fi

# Inside the checkout, which `just bst` mounts at /src; gitignored, never
# under dist/, removed on exit.
dir=.cache-upload-push
rm -rf "${dir}"
trap 'rm -rf "${dir}"' EXIT
(umask 077 && mkdir "${dir}" \
    && printf '%s\n' "${CASD_CLIENT_CERT}" > "${dir}/client.crt" \
    && printf '%s\n' "${CASD_CLIENT_KEY}" > "${dir}/client.key")

mapfile -t targets <<< "${elements}"

connection_config='connection-config: {keepalive-time: 180, retry-limit: 5, retry-delay: 1000, request-timeout: 180}'

# An empty cache directory, so every candidate's state comes from the
# remotes: the ones project.conf and the junctioned projects recommend, plus
# this cache's pull endpoint, which BuildStream otherwise only consults for
# this project's own elements.
cat > "${dir}/probe.conf" <<EOF
cachedir: /src/${dir}/probe-cache
logdir: /src/${dir}/probe-logs
artifacts:
  servers:
    - url: ${pull_url}
      ${connection_config}
EOF

echo "Probing build cache state for ${#targets[@]} key-free elements..."
mapfile -t cached_unpushed < <(
    BST_FLAGS="--config /src/${dir}/probe.conf" \
        just bst artifact show --deps none "${targets[@]}" 2>&1 \
        | sed 's/\x1b\[[0-9;]*m//g' \
        | awk '/^not cached/ {print $3}' || true
)

# If the probe fails entirely (network down, credentials expired), keep the
# previous behaviour of doing nothing rather than uploading blindly.
probe_status="${PIPESTATUS[2]:-0}"
if [ "${probe_status}" -ne 0 ] && [ "${#cached_unpushed[@]}" -eq 0 ]; then
    echo "::warning ::cache-upload: bst artifact show failed; nothing uploaded"
    exit 0
fi

if [ "${#cached_unpushed[@]}" -eq 0 ]; then
    echo "All ${#targets[@]} key-free artifacts are already cached remotely; nothing to upload."
    exit 0
fi

auth=""
if [[ "${push_url}" == https://* ]]; then
    auth="
          auth:
            client-key: /src/${dir}/client.key
            client-cert: /src/${dir}/client.crt"
fi

# FSDK's junctioned elements (zfs opens, k0s, …) need the push remote
# configured for their project; ours can use the default project. Emit one
# block per project we have to push.
{
    echo "projects:"
    cat <<EOF
  default:
    artifacts:
      servers:
        - url: ${push_url}
          push: true
          ${connection_config}${auth}
EOF
    if [[ "${push_url}" == https://* ]]; then
        cat <<EOF
  freedesktop-sdk:
    artifacts:
      servers:
        - url: ${push_url}
          push: true
          ${connection_config}${auth}
EOF
    fi
} > "${dir}/push.conf"

echo "BuildStream push configuration:"
cat "${dir}/push.conf"
echo "Uploading ${#cached_unpushed[@]} of ${#targets[@]} key-free artifacts to ${push_url}:"
printf '  %s\n' "${cached_unpushed[@]}"

BST_FLAGS="--config /src/${dir}/push.conf --on-error continue" \
    just bst artifact push --deps none "${cached_unpushed[@]}"