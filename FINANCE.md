# Kibbutznik Finance — Design Exploration

> Status: DESIGN ONLY. No code in this commit. Review, pick a
> direction, then we implement. Written to answer the user's brief:
> *"each community has a wallet (filled by membership fees), every
> action has a wallet, actions can create funding requests from their
> parent, leaves can pay outside, closed actions return their funds to
> the parent, and there needs to be a way to accept external payment
> (e.g. a dividend)."*

---

## 1. The model we want, in Kibbutznik terms

The brief maps cleanly onto the existing action-tree:

```
                 ROOT KIBBUTZ (wallet)
                    │
         membership fees IN (recurring or one-time)
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
    ACTION A     ACTION B     ACTION C   ← children; each has its own wallet
   (wallet)     (wallet)      (wallet)
       │            │
       ▼            ▼
    ACTION A1    ACTION A2    ← grand-children; still wallets
   (wallet)     (wallet)
                    │
                    ▼
            ┌───────┴───────┐
            │  PaymentOut   │  ← a leaf action spends outward
            │  (to vendor)  │
            └───────────────┘
```

Money flows:

| Flow | Initiated by | Proposal type | Effect on ledger |
|---|---|---|---|
| Membership fee IN | Human (or scheduler) | *auto* or `Funding` | `+fee` on root wallet, `-fee` from the member's external funding source |
| Parent → child grant | Child action | `Funding` filed in parent | on accept: `-amt` parent wallet, `+amt` child wallet |
| Child close → parent sweep | System, when action's `EndAction` is accepted | *automatic* | `-balance` from child, `+balance` into parent |
| External payment OUT | Leaf action | `Payment` (extend existing enum) | `-amt` from action wallet, `+amt` to external payee (rails-dependent) |
| External payment IN (dividend, grant, refund) | External sender | `Deposit` (new proposal type? or webhook) | `+amt` on the receiving community wallet |
| Dividend to members | Any community | `Dividend` (enum already exists) | `-sum` from wallet, `+share` to each active member's internal balance or external payout |

Two invariants to hold everywhere:
1. **Conservation**: no proposal can create or destroy units except
   `Funding` (external in) or `Payment` (external out) at a root/leaf.
2. **Ledger-first**: every balance change is a row in a
   `ledger_entries` table with `{from, to, amount, proposal_id, round_num}`.
   Balances are views over the ledger — never mutated directly.

---

## 2. The hard choice: what is "money"?

This is the fork in the road. All the governance plumbing above is
schema + SQL. The question is what sits underneath "amount" — pure
credits, fiat, or crypto. Tradeoffs:

### Option A — **Internal credits only** (no real money)

- Unit: an abstract Kibbutznik credit. Not redeemable for anything.
- Every flow above works at the ledger level.
- Closest prior art: Open Collective's old "virtual credit" mode,
  time-banking systems (hOurworld, TimeBanks USA), Ithaca HOURS.
- **Pros**: zero regulatory burden. Ship in one sprint. No KYC, no
  state money transmitter licensing, no tax 1099 obligations. Works
  globally.
- **Cons**: nobody cares until we back them with something. Works for
  labor/reputation/attention but not "pay this invoice".

### Option B — **Custodial fiat via a banking-API partner**

- Each community gets a segregated balance inside a platform account
  we own at a partner bank.
- **Candidates:**
  - **Stripe Connect (Custom accounts)**: we become the platform,
    community-as-sub-account. Stripe handles KYC + card acquiring.
    $2.9% + $0.30 per membership fee card charge.
    We'd owe a **money transmitter license** in most US states unless
    we limit money flow to "marketplace transactions" (narrow MTL
    exemption).
  - **Mercury / Lili Treasury**: business bank API. We'd hold a single
    account with sub-ledgers on our side. Cleaner legally, but WE'RE
    the custodian — agent/member disputes land on us.
  - **Wise Business**: multi-currency, good for international. Same
    custody issue.
  - **Increase**: programmatic US banking rails (ACH, RTP, wires).
    Developer-friendly, same MSB exposure.
- **Pros**: real dollars; users immediately understand.
- **Cons**: we become a money-services business. FinCEN
  registration + surety bonds + state MTLs (~$300k–$2M total to cover
  all 50 states) + ongoing compliance (AML, KYC, SAR filings) +
  annual audits. **Not a first-year move for a two-person team.**

### Option C — **Open Collective as fiscal host** (partner, don't build)

- **Open Collective** already solves "transparent community treasury
  with expense approvals" with real USD/EUR, and has a nonprofit
  fiscal host (OSC → Open Source Collective, Open Collective Europe)
  that handles all the money-transmitter + banking pieces.
- Integration model: each Kibbutznik community creates an Open
  Collective project. Accepted `Payment` proposals trigger an
  OC expense via their API. Contributions (incoming dividends,
  funding) flow through OC's existing donation pages.
- OC takes ~5–15% of transaction volume as host fee (project-dependent).
- **Pros**: no MSB status on our side, proven compliance. Communities
  get real money + IRS-compliant receipts (if hosted under OSC which
  is a 501(c)(6)).
- **Cons**: dependency on a third party. OC's governance model is
  different from ours — they use a "host → collective → projects"
  hierarchy; our action tree is deeper. Mapping is possible but
  opinionated. OC has had funding wobbles themselves (layoffs 2024).

### Option D — **On-chain wallets** (non-custodial)

- Each community == a smart-contract wallet (Safe / Gnosis multisig,
  or a specialized DAO treasury contract).
- Members get multisig keys or voting tokens; proposals above a
  threshold translate into on-chain executions.
- **Candidates:**
  - **Safe (Gnosis)** on Optimism/Arbitrum/Base — cheapest rails;
    Safe is the default DAO treasury. We'd use Safe modules
    (Zodiac Reality / Zodiac Roles) so accepted proposals
    auto-execute without manual multisig signing.
  - **Aragon** — DAO stack with built-in governance. Our pulse model
    doesn't map perfectly but Aragon OSx plugins are flexible.
  - **Colony v3** on Arbitrum — hierarchical domains built-in,
    conceptually very close to our action tree (see §4 below).
- Unit: USDC (stablecoin) or the native chain token.
- **Pros**: no custody = we're not an MSB. Global by default.
  Transparent by cryptographic default. Composable with other DeFi.
- **Cons**: gas fees on every transfer ($0.01–$0.50 on L2s; $5–$50
  on mainnet). Key management UX is brutal for non-crypto users.
  Regulatory grey area for KYC-less transfers in some jurisdictions.
  Kibbutznik UI suddenly needs web3 libraries.

### Option E — **Hybrid: internal credit ledger, optional on-/off-ramp**

- Ledger is internal credits (Option A) for all governance plumbing.
- Communities can optionally "back" their credit balance via an
  integration: connect a Stripe Checkout, an OC project, a Safe, or
  whatever. The backing is per-community, not per-platform.
- Each integration is a plugin with a well-defined interface:
  ```
  on_membership_fee_received(community, amount) -> ledger entry
  on_payment_proposal_accepted(community, amount, payee) -> external call
  ```
- **This is our recommendation.** Details in §5.

---

## 3. What counts as a "financial entity" in each option?

A big part of the question is *legally*, what holds the money.

| Option | Legal entity holding the money | Tax status | Members' tax exposure |
|---|---|---|---|
| A. Credits only | — (not money) | — | Probably none; credits aren't income |
| B. Custodial fiat (us) | Kibbutznik Inc. (we'd need to exist as a company + probably an MSB subsidiary) | Ours is C-corp; community is a customer relationship | Fees might be 1099-misc |
| C. Open Collective fiscal host | OSC / OCE (they are 501(c)(6)/(3); member transactions pass through) | Communities = OC projects, get US receipts | Contributors may deduct; payees get 1099s from OSC |
| D. On-chain | No legal entity. Wallet is owned by the key-holders collectively. Some jurisdictions treat that as a partnership by default. | Each member has a taxable position on any transfer | Ambiguous; safest path is wrap in an LLC or unincorporated nonprofit. Wyoming DAO LLC is explicitly for this. |
| E. Hybrid | Credits: none. Backing: varies per integration (B, C, or D above, per community). | Per-integration | Per-integration |

**Wyoming DAO LLC** (since 2021) is the cleanest legal wrapper for
Option D — you can register a DAO as an LLC with "algorithmically
managed" status. Costs ~$100 to form + $60/yr. Becoming one allows
the community to open a bank account in its own name even without
crypto. Similar: **Marshall Islands DAO LLC** (no US tax exposure),
**Utah LLD** (Limited Liability DAO).

---

## 4. Prior art worth stealing from

Three projects have thought more carefully about community treasuries
than we will for a while. Worth reading before we commit:

### Open Collective (https://opencollective.com)
- **Transparent treasury**: every expense has approver + receipts on a
  public page.
- **Fiscal host model**: a compliant nonprofit (OSC, OCE) holds the
  money; the collective directs spending.
- **"Conversations" + "Expenses" + "Tiers"**: closest thing to our
  proposals. Their budget UI is a model worth copying.
- **Integration path**: their GraphQL API supports expense creation
  with a backed-by Collective model. Could be our entire Option C.

### Colony v3 (https://colony.io)
- Treasury per **domain** (their word for action / sub-community).
- **Funding pot**: funds flow down the hierarchy; `moveFundsBetweenPots`
  is literally the parent→child grant in our model.
- **Expenditures**: their version of our Payment proposal, multi-sig
  approval via reputation tokens.
- **Cross-chain now** (Polygon, Arbitrum, Gnosis Chain).
- Their data model is eerily close to ours. If we go Option D,
  staring at Colony's solidity contracts for a day would save weeks.

### Gnosis Safe + Zodiac (https://zodiac.wiki)
- **Safe** is the standard multisig treasury.
- **Zodiac Reality Module** lets off-chain governance (Snapshot, etc.)
  execute on-chain without requiring signers to sign every tx.
- The pattern: "when proposal X hits threshold, any bot can call
  `execute()` with proof, module runs the pre-committed action".
- Our pulse could drive a Reality Module if we chose Option D.

### DAOhaus / Moloch v3
- Summoning + ragequit — members can pull their pro-rata share and
  leave. Interesting for Option A+D hybrid.

### Mirror / JuiceBox
- Crowdfunding for projects, on-chain. Less fit for our model but
  good UX reference for "external party sends money in".

---

## 5. Recommendation: phased, Option E (hybrid)

### Phase 1 (MVP, 2 weeks): credits-only (Option A)

Ship the whole flow — wallets, funding requests, parent→child grants,
Payment proposals, action-close sweep, external deposit form — as a
pure internal ledger. No real money. No regulatory exposure.

**Schema:**
```sql
wallets (
  id UUID PK,
  owner_kind TEXT CHECK (owner_kind IN ('community','action')),
  owner_id UUID,
  balance NUMERIC(18,6) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(owner_kind, owner_id)
);

ledger_entries (
  id UUID PK,
  from_wallet UUID NULL,     -- NULL = external world (deposit in)
  to_wallet   UUID NULL,     -- NULL = external world (payment out)
  amount      NUMERIC(18,6) CHECK (amount > 0),
  proposal_id UUID NULL,     -- which proposal authorized this
  round_num   INT NULL,
  external_ref TEXT NULL,    -- stripe charge id, resend email id, tx hash, etc.
  memo        TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
```

`balance` is denormalized for fast reads; a trigger / periodic job
reconciles against `SUM(ledger_entries)`.

**Proposal types to add to the enum (or reuse existing):**
- `Funding` — already in enum; execution handler debits source wallet
  (parent or external-in), credits target wallet.
- `Payment` — already in enum; execution handler debits leaf wallet,
  logs ledger entry with `to_wallet=NULL` + `external_ref=NULL`
  (Phase 1 = no real external call).
- `Dividend` — already in enum; execution handler splits among
  active members into per-member credit balances.
- `Deposit` — **new**; inverse of Payment. Accepted "Deposit proposals"
  credit the community wallet. For phase 1 this is just a form
  anyone can submit; a moderator / the full community pulse accepts.

**API (new):**
```
GET  /communities/{id}/wallet           → {balance, recent_entries[]}
GET  /actions/{id}/wallet               → same
GET  /communities/{id}/ledger           → paginated entries
POST /communities/{id}/deposits         → file a Deposit proposal
POST /actions/{id}/funding-request      → file a Funding proposal
POST /actions/{id}/payment-request      → file a Payment proposal (leaf-only guard)
```

**UI:**
- New "Treasury" tab on the kibbutz view: balance + recent ledger +
  "Propose deposit" / "Propose payment" / "Request funding from parent".
- Dashboard: my balance across kibbutzim.

**Time**: ~2 weeks end-to-end. All governance plumbing, zero money
risk.

### Phase 2 (4 weeks): pluggable integrations

Keep the ledger as the source of truth. Add an integration interface:
```python
class WalletBacking(ABC):
    async def on_deposit(community, amount, source) -> external_ref: ...
    async def on_payment(community, amount, payee) -> external_ref: ...
```

Three concrete backings:
- `StripeBacking` — Checkout link for deposits, card-based.
  Community registers its own Stripe account; we never custody.
- `OpenCollectiveBacking` — map deposit/payment to OC
  donation/expense via their GraphQL.
- `SafeBacking` — community links a Safe address; deposits are "send
  USDC to this address", payments get proposed as Safe transactions
  via their API (Zodiac not needed for v1 — humans sign the Safe tx).

Each community picks ONE backing (or stays on pure credits). The
ledger records `external_ref` — the Stripe charge id, OC expense id,
or Safe tx hash — so internal credits are always auditable against
the external rail.

**We are NEVER the custodian.** Communities hold their own Stripe /
OC / Safe accounts.

### Phase 3 (months, optional): compliance-light MTL path

If demand is real and Option C + D aren't enough, we build a custodial
product through **Mercury API + Dwolla** or similar, wrapped in a
Delaware LLC. Costs ~$15k–$40k legal, ongoing AML ~$5k/mo. Only pull
this trigger if multiple communities need us to hold fiat directly.

---

## 6. Open questions to answer before building

1. **Unit of account in Phase 1.** "KBZ credit" feels weak. Options:
   generic "credits", "hours" (time-banking feel), or tenant-branded
   per community. Recommend: generic `credits` at first, let
   communities give them a nickname later.

2. **Membership fee policy.** Flat, tiered, community-set? Recurring
   or one-time? My default: each community sets a `membership_fee`
   variable (reuses `ChangeVariable` proposals), and joining auto-files
   a Funding proposal of that amount. Humans pay via the linked
   backing (Stripe/OC/Safe) at join-time; agents don't pay (their
   balance is just credited by the platform for simulation).

3. **Does the root kibbutz have a wallet?** Yes — but what does "root"
   mean in a federation? For a standalone kibbutz, root = the
   community itself. If federation arrives, we decide later.

4. **Refunds / reversals.** Real money = real disputes. Option A
   ledger needs reverse entries; Options B/C/D push it to the rail's
   chargeback flow.

5. **Action close semantics.** When an `EndAction` proposal is
   accepted:
   - (a) sweep remaining balance to parent instantly.
   - (b) file a follow-up Funding proposal so the sweep is
     proposal-gated too.
   - I lean (a) — closing an action already required a pulse.

6. **Dividends vs payouts.** The `Dividend` type splits equally. Do
   we want weighted dividends (by seniority, by contribution, by
   token holding)? Ignore for MVP; add later via a `dividend_policy`
   variable.

7. **What about sub-actions in the action tree — do they get
   wallets?** Yes; every action has a wallet. Grand-children request
   from parents via Funding proposals recursively. Only leaves can
   file Payment (external out); internal nodes can only file Funding
   (move money down).

---

## 7. Files we'd touch when building Phase 1

- `alembic/versions/<rev>_wallets_and_ledger.py` — new migration
- `src/kbz/models/wallet.py` — `Wallet`, `LedgerEntry`
- `src/kbz/models/__init__.py` — export
- `src/kbz/services/wallet_service.py` — balance, ledger, transfer
  helpers (all SELECT FOR UPDATE + INSERT; no UPDATE on balance)
- `src/kbz/services/execution_service.py` — fill the existing
  `_exec_funding`, `_exec_payment`, `_exec_dividend` handlers (they
  are literal `pass` right now)
- `src/kbz/routers/wallets.py` — the endpoints listed above
- `app/app.js` — new Treasury tab, proposal forms for Deposit /
  Payment / Funding
- `tests/test_wallet_service.py`, `tests/test_ledger.py`, …

---

## Recommendation one more time, in one sentence

**Build Phase 1 (Option A, internal credit ledger) exactly as brief
described, with schema + proposal handlers + UI — 2 weeks of work —
and do not touch real money until a specific community asks us to
back their credits with fiat or crypto.** At that point, add a single
integration via Option E — likely Open Collective first (lowest
compliance burden).

---

## References I found useful while writing this

- Open Collective's [Collective API](https://graphql-docs-v2.opencollective.com/)
- Colony's [funding pot mechanics](https://docs.colony.io/colonysdk/topics/funds)
- Safe's [Zodiac Reality Module](https://zodiac.wiki/index.php?title=Category:Reality_Module)
- FinCEN [money transmitter FAQ](https://www.fincen.gov/money-services-business-msb-registration)
- Wyoming [DAO LLC Act](https://sos.wyo.gov/Business/Docs/DAOFAQ.pdf)
- a16z [crypto tax playbook](https://a16zcrypto.com/posts/article/tax-considerations-dao-operators/)
