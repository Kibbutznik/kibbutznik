# Crypto Roadmap — Phase 2+ on-chain backing for the finance module

> **Status:** DESIGN ONLY. No code yet. Reviewed-and-approved Phase 1
> ships credit-only; this document is what Phase 2 looks like when we
> eventually swap internal credits for on-chain real value.

---

## Context

Phase 1 is live: `communities.variables['Financial']` enables the
module per kibbutz, wallets + ledger + escrow-on-apply + payment +
dividend + webhook-deposit all work in internal credits. The
long-term direction (per the user's brief) is **crypto-native** — the
ledger schema was deliberately chosen to make that swap clean:

- `NUMERIC(18, 6)` — matches USDC's on-chain decimals.
- `external_ref` — first-class column, reserved for tx hashes.
- `wallets.balance >= 0` CHECK — no overdraft; matches on-chain
  conservation invariants.
- `WalletBacking` abstract — `InternalBacking` is the only
  implementation today. Swapping to `SafeBacking` or
  `ColonyBacking` should require zero upstream code changes.
- `Financial` variable value space is already `"safe:<address>"` /
  `"stripe:<acct>"` ready — the factory in
  `src/kbz/services/wallet_backing.py` just needs new branches.

---

## The north star

A community votes to enable finance → the `Financial` variable is
set to `"safe:0x…"` or similar → from that moment:

1. The community's on-chain **multisig treasury** (Safe) is linked.
2. Accepted `Funding` / `Payment` / `Dividend` proposals execute
   **on-chain** (not just ledger rows) via a module that watches
   Kibbutznik pulses.
3. External deposits arrive when someone sends USDC to the Safe
   address; an event relayer translates the transfer into our
   `/webhooks/wallet-deposit` shape.
4. Our internal ledger stays in sync — every on-chain tx has a
   mirrored `ledger_entries` row with `external_ref=<tx_hash>`.

The user keeps our UX. The community gets real money. We custody
nothing.

---

## Chain choice

### Layer 2 on Ethereum — cheapest and most supported

**Optimism / Arbitrum / Base** — $0.01–$0.30 per tx, all EVM-compatible,
all support Safe natively. Pick one; the rest of the stack stays
identical. My default recommendation: **Base** (Coinbase's L2, best
on-ramps for newcomer humans via Coinbase Pay).

Alternatives:
- **Polygon PoS** — very cheap, but known as "not quite Ethereum"
  and has had a couple of confidence wobbles. Fine if we want
  lowest fees.
- **Gnosis Chain** — stablecoin-first, natural for community
  treasuries. Smaller ecosystem.
- **Arbitrum Nova** — cheapest of the rollups but less Safe support.

### Not-Ethereum options

**Solana** — much cheaper ($0.0001/tx) but Safe has no Solana deploy;
we'd need a different multisig primitive (Squads). Bigger rewrite.

**Celo** — mobile-first, stable-coin-friendly, but small dev
ecosystem.

**Cardano / Polkadot / Near** — niche; skip unless we have a
jurisdiction-specific reason.

### Unit of account

**USDC** on whichever L2 we pick. Stable, ubiquitous, minted by a
regulated issuer (Circle). No speculative volatility for community
treasuries.

Optional: support the chain's native token (ETH on L2, MATIC) for
gas-only transactions; we never hold speculative positions for
users.

---

## The treasury: Safe + Zodiac Reality Module

### Why Safe

[Gnosis Safe](https://safe.global) is the default DAO treasury. Used
by ~$100B worth of funds across the ecosystem. Every L2 we'd consider
has a deployed Safe factory. Well-audited, battle-tested, familiar
to crypto-native users.

**Per-community setup:**

1. When a community flips `Financial` to `"safe"`, it creates a new
   Safe contract on the configured L2 with threshold 1 signer
   initially (bootstrapped by the kibbutz's founder).
2. The founder shares multisig ownership with a **Zodiac Reality
   Module** — a smart contract that can execute Safe transactions
   WITHOUT requiring a signer, when an off-chain Kibbutznik pulse
   authorizes it.
3. Optional: add member signers for belt-and-suspenders multisig
   (e.g., 2-of-N signer threshold AS WELL as Kibbutznik pulse
   authorization).

### Why Zodiac Reality

[Zodiac Reality Module](https://zodiac.wiki/index.php?title=Category:Reality_Module)
bridges off-chain governance (like our pulses) to on-chain
execution:

- The community's Safe is configured with the Reality Module as a
  Zodiac Module.
- When a proposal hits pulse acceptance in Kibbutznik, we post an
  **answer** to a pre-committed Reality.eth question: *"Did the
  community approve TX_X?"*
- The Reality Module reads the answer on-chain after a
  challenge-delay window, then **anyone** can call `execute()` —
  including a Kibbutznik bot. The Safe processes the tx.

Net effect: accepted proposals auto-execute on-chain, no human has
to sign anything, no ambient multisig key management.

### Alternative: Colony v3

[Colony](https://colony.io) has everything Safe + Zodiac does, plus:
- **Hierarchical domains** — literal parent/child treasury pots. A
  near-perfect match for our action tree (root community → actions →
  sub-actions each with a pot).
- **Reputation-weighted voting** — Colony weights voters by on-chain
  reputation; we could map seniority/closeness to this.
- **`moveFundsBetweenPots`** — literally the parent→child grant from
  our Funding proposal.

If we go crypto, **Colony v3 might be a better fit than Safe+Zodiac**
purely because their data model already matches ours. Cost: their
stack is more opinionated; we lose some flexibility.

---

## Phase 2 implementation sketch

Total estimate: **4–6 weeks** of focused work, assuming one chain
(Base) + USDC-only + Safe+Zodiac (not Colony).

### Week 1: provisioning

- A human flips their kibbutz to `Financial=safe:<address>` (manual
  paste). We create a new `CommunityChainLink` row storing:
  - chain_id (Base = 8453)
  - safe_address
  - reality_module_address
  - asset contract (USDC on Base)
- Migration for the new table, exported from `kbz.models`.
- Admin-only endpoint: `POST /communities/{id}/chain-link` to
  register an existing Safe, idempotent.

### Week 2–3: read path

New `SafeBacking` implementing `WalletBacking`:

```python
class SafeBacking(WalletBacking):
    def __init__(self, safe_address, rpc_url, asset_contract):
        self._safe = safe_address
        self._rpc = Web3(HTTPProvider(rpc_url))
        self._asset = asset_contract  # USDC ERC-20

    async def balance(self) -> Decimal:
        """Return current USDC balance of the Safe on-chain."""
        raw = await self._asset.functions.balanceOf(self._safe).call()
        return Decimal(raw) / Decimal(10 ** 6)  # USDC has 6 decimals
```

Add a **chain reconciler** — a background task that polls Safe USDC
balance every N blocks and asserts it matches `SUM(ledger_entries)`
for that community. Alerts if drift.

### Week 4: write path

**Inbound (deposit):** add a `SafeEventRelayer` service that
watches the Safe for incoming USDC `Transfer` events via ethers.js
or Alchemy webhooks, translates them into our `/webhooks/wallet-deposit`
shape, and POSTs back to ourselves with the HMAC secret. No
changes to `wallet_service.py`.

**Outbound (Payment / Dividend):** `SafeBacking.on_payment` builds
a Reality.eth answer for the pre-committed question matching the
proposal:

```python
async def on_payment(self, from_wallet_id, amount, payee):
    tx_data = usdc.transfer(payee, amount * 10**6)
    question_id = await reality.submit_answer(proposal_id, tx_data)
    # Wait for challenge period (e.g., 30 min)
    return question_id  # becomes external_ref in the ledger entry
```

A separate `execute_pending_onchain` task polls Reality questions
past their challenge window and calls `RealityModule.executeProposal()`
— any node can do this.

### Week 5: UX

- Kibbutz create form: instead of just a checkbox, let the founder
  pick "internal credits" vs "on-chain Safe" and (if Safe) either
  register an existing Safe address OR let us deploy one for them
  via `SafeFactory`.
- Treasury tab: show the on-chain balance, link to block explorer,
  show pending Reality questions with countdowns.
- Member tab: "Connect wallet" button (via WalletConnect v2) for
  members who want to receive dividends directly rather than as
  internal credits.

### Week 6: audits + mainnet

- Security review of the Safe + Zodiac setup (we don't write
  Solidity, but our flow has to be correct — wrong Reality
  question format = locked funds).
- Dry run on Base Sepolia testnet.
- Seed one real community with $100 USDC.
- Write a runbook for "the Reality module got stuck" etc.

---

## User onboarding — the hard bit

Crypto UX is where most projects die. Two paths:

### Path A — Custodial-style onboarding (recommended for MVP)

- Humans sign in with email (magic link — unchanged).
- We create a **smart-contract account** (ERC-4337 / account
  abstraction) per user, controlled by a passkey stored on their
  device + an email-recovery guardian (us).
- User never sees a seed phrase.
- They can export the key later if they want self-custody.

This gets us "Coinbase Wallet"-level UX. The tradeoff is we're a
**semi-custodian** via the email recovery guardian — legally much
lighter than holding USDC ourselves, but not zero.

Services that do this as-a-service: **Dynamic**, **Privy**,
**Magic.link**. ~$0.10 per user per month.

### Path B — Pure non-custodial (crypto-native users only)

- "Connect wallet" button, WalletConnect v2 → user signs with
  MetaMask / Rainbow / Coinbase Wallet.
- Zero custody on our side. Zero compliance burden. Zero UX help.
- Only works for users who already have a funded wallet on our L2.

My take: **Phase 2 ships Path A; we let Phase 3 add the "connect
wallet" escape hatch for crypto-native users who reject the
abstraction.**

---

## Legal wrappers

Even with non-custodial on-chain treasuries, communities may want a
legal entity wrapping the Safe so they can sign contracts, pay
taxes, own property.

| Wrapper | Fit | Cost |
|---|---|---|
| **Wyoming DAO LLC** | Perfect for algorithmic DAOs; explicitly codifies smart-contract governance as LLC operating agreement. | $100 setup + $60/yr |
| **Marshall Islands DAO LLC** | Non-US, minimal reporting, no corporate tax. Good for international communities. | ~$900/yr |
| **Utah LLD** (Limited Liability DAO) | Similar to Wyoming. | ~$70 setup + $20/yr |
| **Swiss Verein / German eV** | Traditional non-profit associations, legally well-understood, good for EU-based civic communities. | Varies by canton / state |
| **MIDAO Foundation** | Turnkey service that forms a Marshall Islands DAO LLC + handles admin. | ~$4k setup + $1.5k/yr |

Recommendation for Phase 2: **do not force a wrapper.** Let
communities pick one if they need to sign contracts. Document the
options above in a "going legal" guide. Partner with a vendor like
MIDAO or `otonomos.com` for turnkey registration.

---

## Compliance

**If we follow Path A** (semi-custodial via ERC-4337 with email
guardian):
- We're a "custodial wallet provider" under FinCEN interpretation
  — same legal category as Coinbase. We'd owe MSB registration
  federally + state-by-state licensing (~$2M to fully license).
- PRACTICAL ALTERNATIVE: partner with **Privy** / **Magic.link** /
  **Dynamic**. They take the MSB responsibility as a wallet-as-a-
  service provider. We integrate their SDK; legal risk stays
  with them.

**If we follow Path B** (pure non-custodial):
- Not a money-services business. No MSB registration.
- Communities might fall under FinCEN as money transmitters if
  THEIR activity looks like it — that's their legal problem to
  solve (via a Wyoming DAO LLC, etc).
- We're just "a governance UI."

**Tax reporting:**
- Dividends to US persons over $600/yr are 1099-MISC territory.
  Whoever operates the community treasury (Wyoming DAO LLC, or
  the community itself if no wrapper) owes the filing.
- We could offer a **1099 generation helper** that reads our
  ledger for any community and produces IRS-ready CSVs.

---

## Migration from Phase 1 to Phase 2

Communities already on `Financial=internal` should be able to
upgrade without losing history. Plan:

1. Community votes `ChangeVariable(Financial=safe:0x…)` (or
   `safe.migrate`) with the new Safe address + deposit amount.
2. On acceptance, our migration handler:
   - Reads the current `Wallet` balance for the community.
   - Burns the internal-credit balance (logged, `proposal_id=...`).
   - Emits a migration event for logs.
   - Updates the `Financial` variable.
   - From now on, balance reads come from `SafeBacking.balance()`
     (on-chain), not from the local `Wallet.balance` column.
3. Escrow wallets for in-flight Membership proposals stay on
   internal credits during migration — complete their lifecycle,
   then the community is fully on-chain.

Downgrade from chain → internal is NOT supported. If needed,
the community would drain the Safe manually and file a PayBack
that mints internal credits equal to the drained amount.

---

## Open questions for Phase 2 kickoff

1. **Which chain first?** My recommendation: **Base** — best
   on-ramps, cheap, strong Safe support, Coinbase's ecosystem.
2. **Safe + Zodiac vs Colony v3?** Safe is the neutral default;
   Colony gives us hierarchical domains for free. Colony requires
   porting their reputation model, which overlaps/conflicts with
   our closeness model. Safe + Zodiac is the safer bet.
3. **Custodial vs non-custodial wallet UX?** Path A (via Privy or
   similar) unlocks real adoption. Path B preserves zero-custody
   ideology. My recommendation: Path A for Phase 2, add Path B as
   an opt-in escape hatch in Phase 3.
4. **Do we cover gas for users?** Most small community
   transactions cost <$0.10 on Base. Covering gas via a paymaster
   (ERC-4337) is user-friendly but costs real money at scale.
   Recommend: free up to 10 tx/month per user, then they pay.
5. **Bridges?** Do we support USDC arriving from Ethereum mainnet
   or another L2? Phase 2 says no — one chain only. Bridges are a
   well-known source of bugs; not our first layer to add.

---

## What Phase 1 did to make Phase 2 easy

Cataloging the design decisions so future-us knows what's intentional:

| Phase 1 decision | Why it helps Phase 2 |
|---|---|
| `Financial` variable encodes backing (`internal` / `safe:…` / `stripe:…`) | Switching chains / rails is a ChangeVariable proposal, not a migration |
| `WalletBacking` abstract interface | `SafeBacking` implementation is a drop-in; no upstream code moves |
| `external_ref` + `webhook_event` columns on `ledger_entries` | Tx hashes + Safe events fit into the existing schema |
| `NUMERIC(18, 6)` — not `FLOAT` or `INTEGER` | USDC's 6-decimal precision is native; no rounding translation |
| `balance >= 0` CHECK | On-chain never has negative balances; Phase 1 invariant matches |
| Webhook-signed deposits, not proposal-gated | Phase 2 relayers re-use the same endpoint shape |
| Escrow wallet has `owner_kind='escrow'` + `owner_id=proposal_id` | Phase 2 escrows could hold USDC the same way; swap backing transparently |
| `resolve_backing(value)` factory in `wallet_backing.py` | One line per new backing; InternalBacking stays as the test/dev fallback |

---

## Reading list for whoever picks this up

1. [Safe Docs → Smart Account](https://docs.safe.global/home/what-is-safe)
2. [Zodiac Reality Module walkthrough](https://zodiac.wiki/index.php?title=Category:Reality_Module)
3. [Colony v3 funding pots](https://docs.colony.io/colonysdk/topics/funds)
4. [ERC-4337 account abstraction overview](https://eips.ethereum.org/EIPS/eip-4337)
5. [Privy.io docs](https://docs.privy.io) — semi-custodial WaaS
6. [Wyoming DAO LLC Act FAQ](https://sos.wyo.gov/Business/Docs/DAOFAQ.pdf)
7. [a16zcrypto Tax Considerations for DAO Operators](https://a16zcrypto.com/posts/article/tax-considerations-dao-operators/)
