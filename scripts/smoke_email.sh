#!/usr/bin/env bash
# smoke_email.sh — verify the production magic-link email pipeline.
#
# Usage: ./scripts/smoke_email.sh [RECIPIENT_EMAIL]
#        defaults to autotest+$(timestamp)@kibbutznik.org
#
# This does NOT read an inbox. It proves the round-trip Kibbutznik →
# Resend works:
#   1. Hit /kbz/auth/request-magic-link with the recipient
#   2. Sleep briefly so the log line appears
#   3. Fetch the last 30s of kbz.service logs and extract the
#      Resend-returned message id
#   4. Exit 0 if Resend accepted, 1 otherwise
#
# Requires SSH access to the server for `journalctl` (the Resend API
# key is restricted to send-only so we can't query delivery status
# without SSH).
#
# For a richer check (verify Resend actually delivered, didn't bounce,
# etc.) you'd need an unrestricted key + a GET /emails/{id} call, or a
# webhook subscription for email events.

set -e
RECIP="${1:-autotest+$(date +%s)@kibbutznik.org}"
SERVER="${KBZ_SERVER:-root@157.180.29.140}"
BASE="${KBZ_BASE:-https://kibbutznik.org/kbz}"

echo "→ sending magic link to $RECIP"
T_SEND=$(date +%s)
RESP=$(curl -sS -X POST "$BASE/auth/request-magic-link" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$RECIP\"}")
echo "   API response: $RESP"

echo "→ waiting 3s for log line"
sleep 3

LOG=$(ssh "$SERVER" "journalctl -u kbz --since @$T_SEND --no-pager 2>&1 | grep -i 'resend.*sent\|resend.*fail' | head -3")
if [ -z "$LOG" ]; then
    echo "✗ no Resend log line in the last 30s — check KBZ_EMAIL_BACKEND=resend on server"
    exit 1
fi
echo "$LOG"

if echo "$LOG" | grep -qi 'resend.*sent'; then
    echo "✓ Resend accepted the send"
    exit 0
fi
echo "✗ Resend rejected the send"
exit 1
