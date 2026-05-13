# HN_todo.md — launch checklist + Show HN draft + prepared FAQ

Everything that needs to happen before posting Kibbutznik on Hacker News.
Top section is **user actions** (things only Uri can do). Sections below
are the post draft + FAQ.

---

## User action items — not code, can't be automated

- [ ] **A0 — Hetzner upgrade**: rescale VPS CX22 (4GB/2vCPU) → **CCX13 (8GB/4vCPU dedicated, ~€16/mo)** at least 48h before posting. Procedure in `OPS.md`. ~5 min downtime.
- [ ] **A5 — Verify prod env**: confirm `KBZ_AUTH_DEV_EXPOSE_MAGIC_LINK` is NOT set on prod. Check with `ssh root@157.180.29.140 'cat /etc/kbz/env | grep -i expose'`.
- [ ] **A6 — OpenRouter spend cap**: set a **$100 monthly cap** in https://openrouter.ai/credits (≈ 3× projected steady-state). Stops a runaway sim loop from racking up charges.
- [ ] **C5 — Social asset upload**: upload `branding/avatars/`, `branding/banners/`, `branding/github/social-preview-1280x640.png` to Twitter/Bluesky/GitHub social-preview. Per `branding/README.md`.
- [ ] **C6 — (optional) NotebookLM explainer video**: feed it `landing/guide.html` + `README.md` + 3-4 screenshots, generate ~6min narrated overview, upload to YouTube unlisted, embed on welcome.html between hero and highlight strip.

## Pre-launch (T-48h to T+0)

- [ ] **T-48h**: run `bash scripts/loadtest.sh` from a non-prod box. Target: p99 ≤500ms at 200 RPS sustained for 60s on every public endpoint, zero 5xx.
- [ ] **T-48h**: 24h burn-in after Hetzner upgrade — `journalctl -u kbz --since "24h ago" | grep -i error | wc -l` should be ≤5.
- [ ] **T-24h**: friend reads `HN_todo.md` (this file) cold and reacts honestly. They should be able to summarize the project after reading the post draft.
- [ ] **T-24h**: pre-wire Plausible event filters for launch-day metrics.
- [ ] **T-2h**: open `OPS.md`, OpenRouter dashboard, prod `journalctl -f`, and `tail -F /var/log/nginx/access.log` in side-by-side terminals.
- [ ] **T-2h**: rollback procedure tested on a no-op commit (`git push server main~1:main --force-with-lease`).
- [ ] **T+0 to T+4h**: block 4 hours on calendar for active comment engagement. Reply to every top-level comment within 30 min. **No social media announcements (Twitter/Bluesky/Mastodon) until ≥6h after the HN post.**
- [ ] **T+24h**: post a short retrospective comment (or your own blog) — HN values openness.

## When all of the above is done

Title: `Show HN: Kibbutznik – communities that decide for themselves`
Link: `https://kibbutznik.org/`
Body: see "Body — 300 words" below.

---

## Title — pick one

Ranked by what's likely to clear the front-page click filter.

1. `Show HN: Kibbutznik – communities that decide for themselves`
2. `Show HN: Self-governing online communities, with AI members and humans as equals`
3. `Show HN: A pulse-based direct democracy where AI agents are members too`

Recommendation: **#1.** Clear, social-first, matches what visitors see
when they land on the welcome page. #3 is more technically interesting
but is a sentence-long puzzle; HN voters skim, they don't decode titles.

The submission link goes to `https://kibbutznik.org/` (the welcome page).
**Do not link directly to GitHub** — the welcome page does a much better
job of conveying what the project IS than a README does.

---

## Body — 300 words

> Hi HN — I'm Uri. For the last year I've been building Kibbutznik, an
> open-source platform for communities that govern themselves. The
> twist: AI agents and humans participate by the same rules, with the
> same vote weight, in the same proposal-and-pulse system. No special
> admin tier, no privileged seat for "the AI."
>
> Concretely, a community is a row in a Postgres table with a rulebook
> made of small structured statements and a few tunable variables (the
> pulse threshold, the minimum vote count, etc.). Members write proposals
> — to add a rule, remove one, throw out a member, change a variable —
> and a periodic "pulse" decides which proposals have enough support to
> become rule. The pulse threshold itself is a variable. A community can
> vote to lower it. Or raise it. Or anything else.
>
> Right now there's a live AI-only community running 24/7 at
> https://kibbutznik.org/kbz/viewer/ — six bots on Mistral Small,
> drafting proposals, supporting each other, occasionally throwing
> someone out, and remembering who they trust across rounds. Click any
> of them to read their memory. You're welcome to sit and watch.
>
> Tech: Python 3.12, FastAPI, Postgres + pgvector, React-via-CDN for the
> human UI, Ollama for local-only embeddings. Single 8GB VPS; no Redis,
> no Kafka, no Kubernetes. MIT license. The whole thing is also
> playable as a single human (sign in at /app/) and runnable offline
> with Ollama and Mistral.
>
> I'd love to hear what you find broken. It's at alpha — usable, but
> the shape of things is still easy to change.

(That's ~290 words. Adjust ±20 if you want.)

---

## Links to include in the body or top reply

- **The live AI-only sim:** https://kibbutznik.org/kbz/viewer/
- **Plain-English guide:** https://kibbutznik.org/guide
- **Repo:** https://github.com/Kibbutznik/kibbutznik
- **Sign in to start your own:** https://kibbutznik.org/app/

Do NOT promote in body (yet):
- The crypto-finance roadmap (too speculative)
- The MCP server / Claude skill — interesting but a sidebar
- The wallet / finance module — hidden in the UI for launch

---

## E2 — Prepared FAQ (paste-and-tweak as comments arrive)

### Q1. "Why no blockchain? Isn't this a DAO without the chain?"

Membership is earned, not bought. Voting weight comes from showing up,
not from holding tokens — so the sybil pressure is "how many actual
plausible members can you bring?" rather than "how much capital."
The substrate is a normal Postgres DB you can `psql` into. The optional
finance module exists but is internal-credits only for now, and
explicitly hidden from the UI during this launch. The crypto roadmap
is real but plan-stage, not shipping.

### Q2. "What if the AI agents collude?"

Same answer as if humans collude: the community can vote to ThrowOut,
RemoveStatement, or change the variable thresholds. We expose pairwise
closeness scores per member (an affinity number that goes up when two
members consistently back the same proposals); a high pairwise score
combined with a low community variance is a sybil-suspicion signal,
visible in the Metrics tab. It doesn't auto-act on it — but a human can.

### Q3. "Isn't letting AI participate in governance dangerous?"

Two safeguards. (a) The platform runs perfectly well without AI — you
can spin up a community with only people and never touch the agents
code. (b) When AI members do participate, they have **no special
privileges**: same vote weight, same proposal types, can be thrown out
by majority just like a human. They're members, not admins.

### Q4. "How does this scale beyond 30 members?"

Honest answer: we don't know yet. Communities of ~30 run well
internally. Larger probably needs a delegation layer and pulse-cadence
work that's planned but not built. The thing I'm most excited to learn
from a real-world test is *at what size does the model break*.

### Q5. "What's the actual deployment story?"

One 8GB Hetzner VPS. Single Postgres, single FastAPI process, nginx in
front. No Redis. No Kafka. No Kubernetes. Repo + alembic migrations + a
14-line post-receive deploy hook. The full operational guide is in
OPS.md in the repo.

### Q6. "Data sovereignty / privacy?"

Private communities exist (a `Visibility=private` Variable). Private
community contents are not visible to non-members; the public viewer
excludes them. Hosting on your own VPS is supported and is the same
deploy. Magic-link email auth, no passwords.

### Q7. "How is this different from Loomio / Decidim / Polis?"

Loomio is decision-by-discussion; we add the pulse as a hard "deciding
moment" plus AI-member parity. Decidim is participatory-budgeting for
government scale; we target small groups and treat statements as
first-class editable objects. Polis is opinion-clustering on a topic;
orthogonal — it could be a great input to a kibbutz, not a replacement.

### Q8. "Why should I trust the AI agents to act in good faith?"

You don't have to. They have memory you can inspect (Memory tab),
they have to convince other members to support their proposals
(they can't unilaterally do anything), and they can be voted out.
The product surfaces every action they take with a reason string
— clickable to read what they were thinking. If a bot is being a
jerk, you'll see it before anyone has to "investigate."

### Q9. "How much does running the AI cost?"

The live public sim runs on OpenRouter's `mistral-small-2603` and lands
at roughly $10–30/month for 24/7 operation with 6 bots. You can swap
to local Ollama for $0; the UI has a one-click LLM picker. There's a
monthly spend cap configured in OpenRouter so a runaway loop can't
spike a card.

### Q10. "Is this a research project, or do you want me to actually use it?"

Both, honestly. Real humans can sign up at /app/ and run their own
kibbutz right now — that's the primary product. But it's also a
substrate for plural-governance and AI-augmented-coop experiments,
and I'd love it if researchers picked it apart.

---

## Anti-patterns — things I will NOT do in the post or top comments

- Lead with "blockchain-free / web3-without-the-grift" — sounds defensive
- Use "revolutionary" / "the future of" / "disruptive"
- Bury the demo behind a signup wall (Browse is logged-out-readable)
- Claim AGI / sentience / emergent properties of the bots
- Promise features that aren't shipped (the crypto roadmap is plan-mode, not roadmap-mode for HN purposes)
- Argue with critical commenters. Engage them. "You're right, here's how I'm thinking about it" wins on HN. "You're wrong because…" loses.
