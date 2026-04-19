"""WalletBacking — the pluggable real-world-money interface.

Phase 1 ships ONLY `InternalBacking`, which is a no-op wrapper — the
ledger IS the truth, no external rail. Phase 2+ plugs in:

    SafeBacking        on-chain via Gnosis Safe + Zodiac Reality Module
    StripeBacking      custodial fiat via Stripe Connect (if we ever go there)
    OpenCollectiveBacking    Open Collective fiscal host

See CRYPTO_ROADMAP.md for the on-chain design.

Dispatch: each community's `Financial` variable encodes which backing
to use. `"internal"` → InternalBacking. `"safe:0xabc…"` → SafeBacking
configured with that safe address. Factory below; add a branch per
backing in Phase 2+.

The interface is deliberately minimal. All real-world effects funnel
through two verbs:

  - `on_payment(from_wallet, amount, memo)` — the community voted to
    pay someone outside. Phase 1: returns None (log-only). Phase 2:
    Safe proposes a tx, Stripe creates a transfer, etc. Return
    value is the external_ref written to the ledger entry.

  - `verify_webhook(signature, body)` — validate an inbound deposit
    webhook. Phase 1: HMAC-SHA256 against `KBZ_WEBHOOK_SECRET`.
    Phase 2: Stripe's signing scheme, Safe's event filter, etc.
    Returns a dict matching our canonical deposit shape or None.

Everything else (minting credits on verified deposits, moving funds
between internal wallets, escrow) stays in WalletService — it doesn't
depend on the backing.
"""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from decimal import Decimal


class WalletBacking(ABC):
    @abstractmethod
    async def on_payment(
        self,
        *,
        from_wallet_id: str,
        amount: Decimal,
        memo: str | None = None,
    ) -> str | None:
        """Execute an external payment. Return an external_ref (tx
        hash, Stripe charge id, etc.) or None when the backing is
        log-only (Phase 1 default). Log-only = ledger still records
        the burn, but no money actually moves."""

    @abstractmethod
    def verify_webhook(
        self,
        *,
        signature_header: str | None,
        raw_body: bytes,
    ) -> bool:
        """Return True iff the signature authenticates the body.
        Caller then parses the body into our canonical deposit shape."""


class InternalBacking(WalletBacking):
    """Phase 1: no external rail. Payments are log-only; webhooks are
    authenticated with a shared HMAC secret.

    This class is entirely self-contained — no network, no keys, no
    ambient state. Trivially unit-testable.
    """

    def __init__(self, webhook_secret: str):
        self._webhook_secret = webhook_secret

    async def on_payment(
        self,
        *,
        from_wallet_id: str,
        amount: Decimal,
        memo: str | None = None,
    ) -> str | None:
        # Phase 1 log-only: the ledger entry still records the burn,
        # we just don't hit any external service. Return None →
        # caller leaves external_ref empty on the ledger entry.
        return None

    def verify_webhook(
        self,
        *,
        signature_header: str | None,
        raw_body: bytes,
    ) -> bool:
        if not self._webhook_secret:
            # Secret unset → webhook is effectively disabled; reject
            # everything rather than accept blindly.
            return False
        if not signature_header:
            return False
        # Accept either "sha256=<hex>" (GitHub/Stripe convention) or
        # bare "<hex>" — we're not picky.
        expected_hex = hmac.new(
            self._webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        candidate = signature_header.strip()
        if candidate.startswith("sha256="):
            candidate = candidate[7:]
        # constant-time compare
        return hmac.compare_digest(candidate, expected_hex)


# ─── Factory ───────────────────────────────────────────────────────


def resolve_backing(backing_value: str, *, webhook_secret: str) -> WalletBacking:
    """Look up the right WalletBacking for a `Financial` variable value.

    Phase 1 only understands "internal". Phase 2+ will branch on the
    prefix: "safe:…" → SafeBacking, "stripe:…" → StripeBacking, etc.
    For now any non-empty value that isn't exactly "internal" falls
    through to InternalBacking with a log — safe degradation so a
    user can't break their community by misconfiguring the variable.
    """
    normalized = (backing_value or "").strip().lower()
    if normalized.startswith("safe:"):
        # Phase 2+: return SafeBacking(address=normalized.split(":",1)[1])
        import logging
        logging.getLogger(__name__).warning(
            "[WalletBacking] safe:* backing not implemented yet — "
            "falling back to InternalBacking (log-only). See "
            "CRYPTO_ROADMAP.md for the implementation plan."
        )
    elif normalized.startswith("stripe:"):
        import logging
        logging.getLogger(__name__).warning(
            "[WalletBacking] stripe:* backing not implemented yet — "
            "falling back to InternalBacking."
        )
    return InternalBacking(webhook_secret=webhook_secret)
