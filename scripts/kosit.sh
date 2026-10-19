#!/usr/bin/env bash
# Fetch (checksum-verified) and run the KoSIT validator daemon.
#
#   scripts/kosit.sh fetch    download validator + XRechnung configuration into .tools/kosit
#   scripts/kosit.sh start    run the daemon in the foreground on 127.0.0.1:${KOSIT_PORT:-8081}
#   scripts/kosit.sh wait     block until the daemon answers (max ~60 s)
#
# Versions and checksums are pinned here and documented in docs/standards.md.
# Checksums were verified against GitHub's published asset digests on 2026-10-19.
set -euo pipefail

VALIDATOR_VERSION="1.6.3"
VALIDATOR_SHA256="799e64befca97d4080e03608c80b85dd5a5ecc5f4ae4f35d1116ec2855b9a7c9"
CONFIG_TAG="v2026-08-31"
CONFIG_ZIP="xrechnung-3.0.2-validator-configuration-2026-08-31.zip"
CONFIG_SHA256="2530cd107c414511c5d0462ec10f886910395abfca820db82e83d70bf01221a8"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${ROOT}/.tools/kosit"
JAR="${DIR}/validator-${VALIDATOR_VERSION}-standalone.jar"
PORT="${KOSIT_PORT:-8081}"
JAVA="${JAVA:-java}"

verify() { # file expected-sha256
  local actual
  actual="$(sha256sum "$1" | cut -d' ' -f1)"
  if [[ "${actual}" != "$2" ]]; then
    echo "checksum mismatch for $1: got ${actual}, want $2" >&2
    rm -f "$1"
    exit 1
  fi
}

fetch() {
  mkdir -p "${DIR}"
  if [[ ! -f "${JAR}" ]]; then
    curl -sSfL -o "${JAR}" \
      "https://github.com/itplr-kosit/validator/releases/download/v${VALIDATOR_VERSION}/validator-${VALIDATOR_VERSION}-standalone.jar"
  fi
  verify "${JAR}" "${VALIDATOR_SHA256}"
  if [[ ! -f "${DIR}/${CONFIG_ZIP}" ]]; then
    curl -sSfL -o "${DIR}/${CONFIG_ZIP}" \
      "https://github.com/itplr-kosit/validator-configuration-xrechnung/releases/download/${CONFIG_TAG}/${CONFIG_ZIP}"
  fi
  verify "${DIR}/${CONFIG_ZIP}" "${CONFIG_SHA256}"
  rm -rf "${DIR}/config" && mkdir -p "${DIR}/config"
  unzip -q -o "${DIR}/${CONFIG_ZIP}" -d "${DIR}/config"
  echo "KoSIT ${VALIDATOR_VERSION} + configuration ${CONFIG_TAG} ready in ${DIR}"
}

start() {
  exec "${JAVA}" -jar "${JAR}" -s "${DIR}/config/scenarios.xml" -r "${DIR}/config" \
    -D -G -H 127.0.0.1 -P "${PORT}"
}

wait_ready() {
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null -X POST --data '<probe/>' "http://127.0.0.1:${PORT}/"; then
      echo "KoSIT daemon ready on port ${PORT}"
      return 0
    fi
    sleep 1
  done
  echo "KoSIT daemon did not start" >&2
  exit 1
}

case "${1:-}" in
  fetch) fetch ;;
  start) start ;;
  wait) wait_ready ;;
  *) echo "usage: $0 fetch|start|wait" >&2; exit 2 ;;
esac
