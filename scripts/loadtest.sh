#!/usr/bin/env bash
# loadtest.sh — pre-launch load smoke for kibbutznik prod
#
# Run this from a non-prod machine 48h before the HN launch so we know
# what our actual ceiling looks like.
#
# Requires: wrk (`brew install wrk` on macOS, `apt install wrk` on debian).
#
# Targets (from the launch-readiness plan, section H):
#   - p99 ≤ 500ms at 200 RPS sustained for 60s
#   - zero 5xx
#   - zero socket errors

set -euo pipefail

HOST="${HOST:-https://kibbutznik.org}"
DURATION="${DURATION:-60s}"
THREADS="${THREADS:-4}"
CONNECTIONS="${CONNECTIONS:-100}"

# Endpoints that anonymous traffic actually hits during a launch surge.
# Each line: a short label, then the path.
PATHS=(
  "welcome    /welcome.html"
  "landing    /"
  "guide      /guide.html"
  "ecosystem  /ecosystem.html"
  "highlights /kbz/highlights"
  "memory     /memory.html"
)

echo "Load smoke against ${HOST}"
echo "  duration: ${DURATION}  threads: ${THREADS}  connections: ${CONNECTIONS}"
echo

fail=0
for entry in "${PATHS[@]}"; do
  label="${entry%% *}"
  path="${entry##* }"
  url="${HOST}${path}"
  echo "──────── ${label}  ${url}"
  # --latency adds the p50/p75/p90/p99 line we care about
  if ! out=$(wrk -t"${THREADS}" -c"${CONNECTIONS}" -d"${DURATION}" --latency "${url}" 2>&1); then
    echo "FAIL: wrk could not run against ${url}"
    fail=$((fail+1))
    continue
  fi
  echo "${out}"
  # Grep for the p99 line and flag if it's > 500ms
  p99=$(printf "%s\n" "${out}" | awk '/99%/ { print $2 }')
  if [ -n "${p99}" ]; then
    echo "  → p99: ${p99}"
  fi
  # Flag any non-2xx counted by wrk
  errs=$(printf "%s\n" "${out}" | awk '/Non-2xx or 3xx responses:/ { print $4 }')
  if [ -n "${errs}" ] && [ "${errs}" != "0" ]; then
    echo "  ⚠ Non-2xx responses: ${errs}"
    fail=$((fail+1))
  fi
  echo
done

if [ "${fail}" -gt 0 ]; then
  echo "❌ ${fail} endpoint(s) had errors. Investigate before launch."
  exit 1
fi
echo "✅ All endpoints completed without non-2xx errors."
echo "   Manually check p99 numbers against the 500ms launch target."
