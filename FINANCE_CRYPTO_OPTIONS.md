# Finance → Crypto: Deployment Options

> **Status:** Plan-mode draft. No code written yet. Pick a lane, then I'll scaffold it.
>
> **Goal:** take the already-working internal-credits finance module (wallets · ledger · escrow · webhooks) and give communities a real on-chain option — without forcing crypto on communities that don't want it.

---

## 0. Context — what already exists today

| Piece | Status | Where |
|---|---|---|
| Per-user & per-community wallets (`Wallet` rows) | ✅ live | `src/kbz/models/wallet.py`, `services/wallet_service.py` |
| Append-only ledger (`LedgerEntry`, double-entry-ish) | ✅ live | `services/wallet_service.py::mint/transfer` |
| Membership-fee escrow on `/apply` → release on accept, refund on reject | ✅ live | `services/community_application_service.py` |
| Deposit webhook (HMAC-verified) | ✅ live | `routers/webhooks.py` |
| Welcome-credits gift | ✅ live | `routers/auth.py::_provision_welcome_credits` |
| Module toggle via community `Variable` `Financial=internal` | ✅ live | `services/wallet_service.py::_financial_gate` |
| **Backing abstraction** — `WalletBacking` interface with "internal" impl | ✅ live (stub for more) | `services/wallet_backing.py` |

The `WalletBacking` abstraction is **the key**. Switching rails = writing a new `WalletBacking` impl and pointing `KBZ_WALLET_BACKING` at it. The wallet service, ledger, escrow, dashboards, and agents do not need to change.

---

## 1. The three real options

### ░░ Option A ░░ · Stablecoin rails on an L2
**Pitch:** credits become USDC on Base. Users see dollar amounts. Platform holds one hot wallet per community; custody is "we run it for you, with audit."

- **Best for:** the 95% case. Membership dues, small prize pools, communities that want real value without exotic mechanics.
- **User experience:** same as today — a balance number, a "deposit" button (which routes through a managed on-ramp like Circle's `USDC` buy-flow or a Coinbase Commerce link). Users never need a wallet app.
- **Ops burden:** low. Base has <$0.01 tx fees. One hot wallet per community with multisig recovery is straightforward (Safe{Core} or Privy server wallets).
- **Trust model:** custodial. The platform holds private keys. This is totally fine for "run a homeowner's association" but wrong for "run a treasury nobody controls."

**Why start here:** credits translate 1:1 to USDC, no new UX for the user, the existing escrow flow works identically (hold funds at platform level, release on pulse outcome).

### ░░ Option B ░░ · Gnosis Safe per community
**Pitch:** each community that turns on finance gets its own Gnosis Safe. Members add their wallets as signers over time; the community's own governance decides who is a signer. The KBZ backend submits transactions to the Safe; humans confirm them with their wallet apps.

- **Best for:** slower, higher-trust, higher-value communities. DAO-adjacent groups. Treasuries where the answer to "who can move the money" cannot be "the platform operator."
- **User experience:** real. Members install a wallet (MetaMask / Rabby / Frame), sign transactions, pay gas. This **is** the crypto-native flow.
- **Ops burden:** medium. Safe tooling is mature; `safe-core` SDK handles most of it.
- **Trust model:** fully self-custodied. KBZ becomes a front-end for a set of Safes — powerful and correct, but excludes the non-crypto audience.

**Why this second:** it's the clean long-term answer for serious communities. But it raises the bar for every user from "click a button" to "install a wallet and sign."

### ░░ Option C ░░ · Community-minted ERC-20 tokens
**Pitch:** a community can mint its own ERC-20 to replace credits. Earn the token by contributing, spend it to sponsor proposals, use it as weight for pulse support, redeem it at community-defined rates. Each community becomes its own micro-economy.

- **Best for:** cooperatives, creator collectives, mutual-aid groups, experimental DAOs. Anywhere the act of holding the token *is* membership.
- **User experience:** a balance of `$KIBBUTZ-01` instead of credits. Transfers, staking, optional redemption.
- **Ops burden:** high. Tokenomics questions (supply cap? inflation? redeemable vs pure utility?) are governance decisions the community must make, and the platform has to surface those knobs without requiring a PhD.
- **Trust model:** community-custodied via their own Safe (Option B underneath).

**Why last:** this is the most genuinely novel thing KBZ could ship. It's also the one most likely to attract regulatory scrutiny — "is this a security?" — and needs legal review before live mints.

---

## 2. Recommendation: do all three, in this order

Not really three projects — one project with three increments, each unlocking the next.

```
   Internal credits       Option A           Option B           Option C
   (LIVE today)  ────►  USDC on Base  ────► Safe per       ────► Mintable
                                             community            community
                                                                  token
    ~1 mo         ~3 wks             ~5 wks              ~6 wks
```

Each step keeps the previous one working. A community can stay on internal credits forever. Another can move to USDC. Another can bolt a Safe underneath. Another can mint a token that *uses* the Safe. The menu grows; nothing breaks.

---

## 3. Phased plan (plan-mode)

### Phase 2A · Stablecoin rails
*Target: first community can take a real $10 USDC membership fee.*

- [ ] **Pick a chain.** Base (Coinbase L2, USDC is native, Circle-issued). Fallback: Polygon PoS.
- [ ] **Custody provider decision.** Privy server wallets (managed, easy) vs. self-hosted hot wallet (Safe + signer in env). Pick Privy for v1.
- [ ] **New `WalletBacking` impl.** `UsdcOnBase(WalletBacking)` in `services/wallet_backing_usdc.py`. Same interface: `credit`, `debit`, `balance`. Internally calls Privy.
- [ ] **Deposit UX.** Two lanes:
  - Embedded on-ramp via Privy's Coinbase integration (fiat → USDC, 1-minute flow).
  - Existing webhook path (external wallet pushes USDC → address, webhook credits the ledger).
- [ ] **Withdraw UX.** New `/wallet/withdraw` → request USDC to an external EOA. Flagged, rate-limited, confirmed by email, 24h delay for amounts > $X.
- [ ] **Fee handling.** Who pays gas? We subsidize for deposits < $100; users pay for withdraws.
- [ ] **Failure mode.** Chain outage / RPC down → existing 300ms timeout pattern: the UI shows "temporarily unavailable", ledger writes are paused, nothing silently corrupts.
- [ ] **Variable value.** `Financial=usdc-base` triggers this backing. Communities can flip between `internal` and `usdc-base` via normal proposals (but only when the community's total balance = 0, to avoid migrations).

**Hard problems:**
- Gas estimation in escrow flows (we hold USDC; we also need to cover gas when releasing escrow). Solution: maintain a small ETH float on each hot wallet, replenish via cron.
- Dust handling — sub-cent amounts don't round cleanly. Solution: mint fractional internal credits backed by on-chain USDC; round up at withdraw time.
- KYC. At what deposit threshold does a community need to KYC its members? Defer — v1 caps single-deposit at $99 which sits under every regulator's radar.

### Phase 2B · Gnosis Safe per community
*Target: a community can opt into fully self-custodied money, platform cannot move funds unilaterally.*

- [ ] **`SafeBacking(WalletBacking)`.** All "mint / transfer / escrow" operations become Safe transaction proposals. The service submits; signers confirm; execution happens when threshold hits.
- [ ] **Signer bootstrap UX.** When a community flips `Financial=safe`, the UI walks whoever flipped it through: (a) connect wallet, (b) deploy a new Safe owned by [themselves + platform-recovery-key], (c) invite other members to join as signers over time via normal proposals.
- [ ] **Threshold via Variable.** `Financial.safe.threshold=3of5` becomes a community-tunable variable — changing it is a proposal like any other. Governance-as-code.
- [ ] **Pending-tx UI.** Every outgoing Safe tx shows up in the community dashboard with a "sign" button for signers. Use Safe's existing event stream; don't invent our own.
- [ ] **Platform as optional safety net.** The platform holds ONE recovery key, never used unless the community explicitly votes to invoke it. Transparent by design.

**Hard problems:**
- Signers must install a wallet. Inescapable — Safe = self-custody = real signatures. We'll offer Privy's embedded wallet for members who don't have one, with an explicit "this is still your key" disclaimer.
- Adding/removing signers is itself a Safe tx. Explain this clearly; it's a feature (cryptographic rigor), not a bug.

### Phase 2C · Community-minted ERC-20
*Target: a community can mint its own token, set emission rules, use it as pulse weight.*

- [ ] **Token factory contract.** Single audited factory deployed to Base; communities call `mint(name, symbol, supplyRules)`. Produces a plain ERC-20Votes contract (inherit OZ's battle-tested impl).
- [ ] **Supply models.** Menu of three:
  - **Fixed supply** — all tokens minted up front to the community treasury, distributed via proposals.
  - **Contribution-based** — members earn on actions (support count, proposals authored, time in community). Mint/emission on-chain or platform-side?
  - **Membership-linked** — 1 member = 1 token, auto-burned on leave.
- [ ] **Pulse weighting.** `Financial.token.pulse_weight = linear | sqrt | logN | one-person-one-vote` (last one ignores token balance). Make the choice *visible* on every proposal so manipulation is obvious.
- [ ] **Redemption.** Optional: the community can vote to redeem tokens against their Safe's USDC balance. This is real liquidity and real regulatory risk — ship it behind a "Legal review required" flag initially.
- [ ] **Secondary market.** Don't build one. If a community wants Uniswap, they can create a pool themselves — we link to the Uniswap page but don't wrap it.

**Hard problems — legal & economic, not technical:**
- **Securities classification.** In most jurisdictions, a token you can buy, sell, or redeem for money is a security. The redemption feature is the trip-wire. Ship utility-only first (no cash redemption); add redemption behind per-community legal opt-in later.
- **Inflation tantrums.** Founder mints a million tokens, dumps on members. Mitigation: supply rules are baked into the token contract at mint time and cannot be changed. Community agrees to the rules before minting; after, not even the community can break them.
- **"My token is worth nothing."** Most tokens tend this way. Set expectations hard — a community token is a membership signal, not an investment.

---

## 4. What changes for users

| Today (internal credits) | Option A (USDC) | Option B (Safe) | Option C (token) |
|---|---|---|---|
| Sign up, get 100 free credits | Same + "buy USDC" button | Same + "connect wallet" step for signers | Same + "earn $COMMUNITY" for participation |
| Pay a community fee = credits deducted | Pay a community fee = USDC held in platform escrow | Pay a fee = USDC held in community Safe | Pay a fee = tokens locked, released on accept |
| Withdraw: impossible | Withdraw: USDC to any EOA | Already self-custody | Token-native: transfer to other holders |
| Wallet app: none | Wallet app: optional | Wallet app: required for signers | Wallet app: required |
| Regulatory exposure: zero | Low (< $100/tx) | Zero (self-custody) | Medium-to-high |

Key principle: the **same community** can host all four kinds of members. A crypto-native signer on Option C still sees the same UI as the credit-only lurker; only the backend backing differs per-account/per-community.

---

## 5. What I'd build if you green-light this tomorrow

1. **Week 1:** `WalletBacking` interface audit + integration test harness. Make sure nothing in the app assumes the `internal` backing; any assumption becomes a bug we fix before onboarding a real rails.
2. **Week 2:** Privy integration spike. Smallest possible "deposit USDC → see balance on KBZ" loop.
3. **Week 3-4:** `UsdcOnBase` backing + new deposit UX + new withdraw flow + fee pipeline.
4. **Week 5-7:** Safe integration. Feature-flag it behind `Financial=safe-experimental`. Invite 2-3 friendly communities to pilot.
5. **Week 8+:** evaluate if anyone actually uses B before starting C.

**Total wall-clock to Phase 2A live:** ~4 weeks of focused work.
**Total to all three:** ~3 months.

---

## 6. Decisions I need from you

| # | Decision | Options | My lean |
|---|---|---|---|
| 1 | Chain for Phase 2A | Base · Polygon · Optimism · Arbitrum | **Base** (native USDC, Coinbase brand trust, <$0.01 gas) |
| 2 | Custody provider | Privy · Safe-from-day-1 · DIY hot wallet | **Privy** (managed, mature, easy) |
| 3 | Fiat on-ramp | Coinbase Commerce · Stripe Crypto · MoonPay · none (crypto-only) | **Coinbase Commerce** (lowest friction, US+EU) |
| 4 | License for the finance crypto modules | Same as main repo · Stricter (AGPL) for the money parts | **Same as main** — no reason to fork the license model |
| 5 | Legal review timing | Before Phase 2A · Before Phase 2C (redemption) · Never | **Before 2C only** — 2A is a remittance of funds users already own, low risk |

---

## 7. What we are explicitly **NOT** doing

- Running an exchange, DEX, or AMM
- Issuing a KBZ platform token ("no KBZ coin")
- Custody of tokens held across communities (each community's funds are isolated)
- Cross-chain bridges (stay on one L2 for v1)
- Lending, staking rewards, yield — this is community governance, not DeFi
- Any anonymous membership — every wallet linked to a user account the community knows about

These aren't "maybe later" — they're "no, that's a different product."

---

*Next step: pick items 1-5 in Section 6 above and I'll scaffold Phase 2A. Or tell me to start with Phase 2C (community token) instead if that's the more exciting demo for launch. Up to you.*
