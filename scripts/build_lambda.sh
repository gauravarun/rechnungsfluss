#!/usr/bin/env bash
# Build dist/lambda.zip for the pipeline Lambdas (python3.12 on Amazon Linux 2023, x86_64,
# glibc 2.34, so manylinux_2_28 wheels are compatible).
#
# Dependencies come from uv.lock (runtime group only, no dev tools) as Linux
# wheels, so the zip is reproducible from the lock file on any OS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${ROOT}/build/lambda"
OUT="${ROOT}/dist/lambda.zip"

rm -rf "${BUILD}" && mkdir -p "${BUILD}" "$(dirname "${OUT}")"
${UV:-uv} export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt \
  > "${ROOT}/build/requirements.txt"
${UV:-uv} pip install --quiet --target "${BUILD}" --requirement "${ROOT}/build/requirements.txt" \
  --python-platform x86_64-manylinux_2_28 --python-version 3.12 --only-binary :all:
cp -r "${ROOT}/src/rechnungsfluss" "${BUILD}/"
find "${BUILD}" -name "__pycache__" -type d -prune -exec rm -rf {} +

rm -f "${OUT}"
(cd "${BUILD}" && python -m zipfile -c "${OUT}" .)
echo "built ${OUT} ($(du -h "${OUT}" | cut -f1))"
