# Kibbutznik Marketing Synthesis — Relaunch Report

## Executive Summary

The Show HN didn't fail on quality — it failed on credibility: a 5-week-old org, a 1-star repo with a null description, and a green-username HN account that the algorithm auto-flagged before any neutral reader saw it. The product story itself is sound but currently scattered: there is no canonical one-line pitch, two competing homepages, six unowned audience segments, and the single most defensible wedge (AI members governing under identical rules to humans, with a live 24/7 demo) is buried below the fold on every surface. Meanwhile the competitive landscape just tilted in Kibbutznik's favor — Tally's March 2026 shutdown empirically validates the non-token stance, and no competitor (Loomio, Decidim, Polis, PolicyKit, Snapshot) ships AI-as-equal-member. The relaunch move is not "post again harder" but: recover the flagged post through proper channels, warm a real account over 4-6 weeks, sharpen the pitch around one beachhead (tech worker co-ops), and arrive at the HN retry with visible organic momentum (GitHub stars, Metagov/co-op pilots, a blog trail).

---

## P0 — Do first / blocks the relaunch

### 1. Email hn@ycombinator.com before anything else
**Area:** Immediate recovery
**Evidence:** Second-chance pool exists at news.ycombinator.com/pool; dang responds in 1-2 business days; flagging vs community-kill changes the strategy.
**Recommendation:** Within 48h, send one polite factual email: 2-sentence project description, note it's a genuine open-source side project, include the original submission URL, and ask (a) whether the flag was algorithmic or community, and (b) whether it qualifies for the second-chance pool. Do not ask to be unflagged directly — let dang decide. If the pool is offered, accept immediately.

### 2. Do NOT create a new HN account or repost to a new domain
**Area:** Risk mitigation
**Evidence:** kibbutznik.org is currently clean (200 OK, no domain-level flag). Multi-account patterns from one IP risk a permanent, unappealable domain-level shadowban — strictly worse than a flagged post.
**Recommendation:** Never spin up a second account, never sockpuppet, never repost the same URL from a fresh account. The only safe alternate-submitter path is a trusted colleague with >500 karma who genuinely engages the comments themselves.

### 3. Root cause is account credibility, not content — warm the account
**Area:** Account / credibility
**Evidence:** Org created 2026-04-21, repo has 1 star / 0 forks; new HN accounts show green for <2 weeks and have downweighted votes. The HN_todo.md draft is already strong.
**Recommendation:** Do not resubmit until the account is >4 weeks old with 200-250+ karma earned through substantive comments on unrelated tech threads (AI agents, cooperative economics, distributed systems, FastAPI/Postgres). 5-10 quality comments/week. The draft ships unchanged — only the account needs to mature.

### 4. Two homepages with divergent positioning and canonical confusion
**Area:** Homepage architecture / SEO
**Evidence:** `/` serves welcome.html (warm, "A new shape for community"); `/index.html` (psychedelic edition) is a completely different document but its `og:url` points to root (landing/index.html:21). welcome.html `og:url` points to `/welcome.html` (landing/welcome.html:19). The two pages don't link to each other except a small footer link.
**Recommendation:** Make welcome.html canonical (its "Not a forum. Not a DAO." three-pillar section is the strongest positioning text in the project). Route the psychedelic page to `/explore` with clear "secondary view" navigation, or 301 it. Fix `og:url` on welcome.html to `https://kibbutznik.org/` (see P1 SEO cluster — these are the same fix).

### 5. No canonical one-line pitch exists — every surface differs
**Area:** Core messaging
**Evidence:** At least 8 distinct one-liners across title, meta, hero, slides, README, HN, narration (welcome.html:6-7, 837-842; index.html:1183-1193; README.md:3; HN_todo.md:31; promo/narration.md:50).
**Recommendation:** Pick one 8-12 word slug and propagate it to `<title>`, README H1, og:title/description, HN first sentence, and the MCP skill description. Strongest candidate: **"Pulse, not politics. Members, not managers."** Reserve "engine/substrate/platform" for technical contexts only.

### 6. AI-as-equal-member is a genuine white space — lead with the live demo
**Area:** Differentiation (the wedge)
**Evidence:** Across Loomio, Decidim, Polis, PolicyKit, GlassFrog, Snapshot, Commonwealth, Tally — zero ship AI as a first-class votable member. Closest is Metagov's KOI Pond (a read-only Slack chatbot). Kibbutznik has a running existence proof: viewer/, agents/memory_formatter.py (temporal KG), kibbutznik-mcp/.
**Recommendation:** Lead with the **viewer as proof-point** ("click any member to see what it was thinking"), not the architecture. For HN, keep AI as the *last* hook (HN audiences dislike AI hype) — but on the product site, promote the viewer CTA to first-or-equal in the welcome.html hero (currently third, after "Open the app" and "Read the guide" — welcome.html:844-851).

### 7. Tally's March 2026 shutdown validates the non-token stance — use it
**Area:** Competitive positioning
**Evidence:** Tally (Uniswap/Arbitrum/ENS governance UI, $25B treasury, 1M users) shut down March 2026; CEO: demand was regulatory-avoidance theatre, not genuine governance need. Snapshot still dominates off-chain voting but requires token-gating. (HN_todo.md Q1 predates this.)
**Recommendation:** Add one sentence to FAQ Q1: "The largest dedicated DAO governance platform (Tally) shut down March 2026; its CEO said demand was primarily regulatory-avoidance theatre — which confirms our thesis." Cite when anyone asks "why no blockchain."

### 8. Six unowned audience segments — pick one beachhead: tech worker co-ops
**Area:** Audience / ICP
**Evidence:** welcome.html:937-964 lists six segments plus "eventually, the whole of humanity"; index.html:1196 lists five different ones; HN adds a sixth framing. Loomio's own highest-traction segment is worker co-ops (dedicated /worker-cooperatives/ page); ~1,300 US worker co-ops (USFWC 2024); README already says "AI-augmented coops" (README.md:23). The pulse maps directly onto Loomio's top complaint (threads that drift without a deciding moment).
**Recommendation:** Reframe the hero around **tech-adjacent worker co-ops (10-50 members)**: "Governance for worker co-ops and tech collectives." Keep other segments as narrative warmth lower on the page but build no onboarding around them until 10+ co-op communities run. Remove "…the whole of humanity" — it reads as scope creep to a technical audience.

### 9. First-10-communities reachability map (warm channels, not broadcast)
**Area:** Distribution
**Evidence:** social.coop (~500 Mastodon members, on Loomio, documented governance pain); Comradery (Loomio customer); USFWC member directory; Platform Cooperativism Consortium Slack/newsletter; Metagov Gateway (self-serve tool submission); RadicalxChange Slack. Refs in finding.
**Recommendation:** Sequence: (week 1) submit to Metagov Gateway + email social.coop's Community Working Group with a 3-sentence demo offer; (weeks 2-3) post the viewer link to USFWC + PCC Slacks with a specific ask ("3 co-ops for a 30-day pilot, honest feedback"); (week 4) HN retry. Target: 3 live human communities by week 6, 10 by week 12.

---

## P1 — High value, parallel to the recovery window

### 10. SEO scaffolding is entirely absent (consolidated)
**Area:** SEO / discoverability
**Evidence:** robots.txt and sitemap.xml both 404; no `rel=canonical` on any page; no JSON-LD; www serves duplicate content with no 301 to apex (kbz.conf:148-154); welcome.html og:url says `/welcome.html` not `/` (welcome.html:19); welcome.html missing `twitter:site` (index.html:29 has `@kibbutznik_ops`).
**Recommendation:** One ~2-hour batch: add sitemap.xml + robots.txt with Sitemap pointer; add `<link rel=canonical href="https://kibbutznik.org/">` to every page; fix welcome.html og:url to apex root; add a www→apex 301 server block in deploy/nginx/kbz.conf; add WebSite + SoftwareApplication JSON-LD to welcome.html; add `twitter:site`. These compound with the P0 homepage canonical fix.

### 11. GitHub repo has no description and 1 star — fix before retry
**Area:** Social proof
**Evidence:** `gh api` → description:null, stargazers_count:1. HN_todo.md C5 (social preview upload) still unchecked.
**Recommendation:** Add a repo description, topics (governance, democracy, fastapi, postgres, ai-agents, open-source), and the prebuilt social preview image. Use the warmup window to grow stars to 50+ via the co-op/Metagov/plurality channels so a click-through doesn't read as a throwaway project.

### 12. Karma-building plan (4-6 weeks) + optimal repost timing
**Area:** HN warmup
**Evidence:** Safe threshold 200-250+ karma, account >4 weeks; one front-page comment can yield 20-50 karma/day. Sunday 11-16 UTC window shows ~12-14% breakout vs ~10% weekday; top-10 needs ~8-10 genuine upvotes + 2-3 comments in first 30 min.
**Recommendation:** Weeks 1-4 substantive commenting (target 50-100 karma by week 4), weeks 5-6 reach 200+. Repost target **Sunday ~12:00 UTC**. In the prior week, individually message 5-8 people who've actually seen Kibbutznik: "I'd love an honest reaction Sunday morning" — not an upvote blast. Founder must clear 4 hours to reply to every top-level comment within 30 min (already in HN_todo.md, correct).

### 13. Make hierarchical Actions concrete above the fold
**Area:** Concept clarity
**Evidence:** welcome.html calls pillar 2 "Working groups that stick" (welcome.html:916-923, 992-1003), which undersells the recursive-governance angle. The clearest 2-sentence version exists only in promo/narration.md:37-40.
**Recommendation:** Surface the narration language as the second pillar: "Spin off a working group. Let it run. Its result comes back up for a community vote." This is the hook that distinguishes Kibbutznik from "Loomio plus a timer."

### 14. Viewer is the top demo asset but has no conversion CTA
**Area:** Site-as-funnel
**Evidence:** viewer/app.js:1912-1994 (header has only a back-arrow) and 3541-3595 (intro banner links to guide.html, not /app/). A convinced visitor has no next step.
**Recommendation:** Add a "Start your own →" pill `<a href="/app/">` to the viewer header and a secondary "Want to run your own?" link to the intro banner. Self-contained changes in viewer/app.js.

### 15. "Real communities" copy actually shows only simulation data
**Area:** Content trust
**Evidence:** welcome.html:860-863 says "real communities," but `/kbz/highlights` returns only "AI Kibbutz," "Core Features Drafters," "Why This Project Writers" — all linking to the viewer.
**Recommendation:** Relabel to "From the simulation, right now — the AI kibbutz running 24/7. Human communities are invite-only — start your own to see yours here." Or filter to human communities once they exist. A skeptic who clicks into the viewer instead of a human community will notice and lose trust.

### 16. Prepare the PolicyKit/Metagov FAQ answer
**Area:** Positioning
**Evidence:** HN_todo.md Q7 (lines 163-170) covers Loomio/Decidim/Polis but not "isn't this just PolicyKit?" PolicyKit's last major update was 2023; it never shipped a production SaaS.
**Recommendation:** Add Q11: "PolicyKit is a research prototype last actively developed in 2023; Kibbutznik is a shipped product with real users and a 24/7 demo." Position the Metagov/RadicalxChange orbit as allies (submit to their tool registries), not competitors.

### 17. Parallel channels: Metagov, Lobsters, Fediverse, PCC
**Area:** Distribution (build momentum during warmup)
**Evidence:** Metagov Slack ~1,700 qualified practitioners + weekly seminar + newsletter (highest-fit, low-effort/high-return); Lobsters welcomes technical writeups (not launches, needs an invite + 70-day window); Fosstodon (~49k) and social.coop (runs itself on Loomio) align with the FOSS/co-op values; PCC newsletter (2025 theme "Cooperative AI"). Polis hit 343 pts/159 comments on HN, proving governance tools reach the front page with a credible account.
**Recommendation:** (1) Join Metagov Slack, participate 2-3 weeks, then propose a Wednesday seminar talk and drop the viewer link in show-and-tell. (2) Get a Lobsters invite via Metagov/Fosstodon overlap; write a 1,200-word *technical* essay on the pulse lifecycle and AI-membership design (not a launch). (3) Stand up Fosstodon + social.coop accounts using the prebuilt branding/avatars assets; post a 3-toot thread (what it is + viewer / self-hosting / no-admin co-op angle). (4) Email PCC for a newsletter/resource-library mention.

---

## P2 — Polish and longer-tail

- **Meta description undersells the product** (welcome.html:7 reads like Doodle). Replace with differentiating language, e.g. "Open-source governance where proposals earn support between heartbeats — no elections, no admins, no votes. Humans and AI members, same rules." (~155 chars).
- **Category noun inconsistency** ("engine" vs "platform" vs "substrate" vs "tool" vs "app"). Standardize: "engine" in dev/HN contexts, "app" only in CTAs; drop "substrate"/"platform" from above-the-fold copy.
- **Promo video produced but unpublished** (promo/out/final.mp4, 11.5MB, exists; HN_todo.md C6 unchecked). Upload to YouTube (even unlisted), embed on welcome.html between hero and highlights — shortens skeptics' "is this real?" check.
- **Newsletter signup only on the psychedelic page** (Buttondown iframe at index.html:1989-1994, absent from welcome.html footer). Add a low-commitment subscribe link to welcome.html.
- **Finish the HN_todo.md ops checklist.** A5 (verify `KBZ_AUTH_DEV_EXPOSE_MAGIC_LINK` unset on prod) and A6 (OpenRouter $100 spend cap) are <10-min items — do now. A0 (Hetzner upgrade) should land a full week before retry, not 48h, to allow burn-in before any traffic spike hits the 24/7 AI sim.
- **Five link-bait content pieces** seeded from existing data: "We ran a 24/7 AI parliament for 30 days," "The pulse mechanic: why governance tools fail without a shared deciding moment," "DAO governance without tokens" (maps primitives onto DAO vocab; post to RadicalxChange/Metagov, not the landing page), "What 200+ simulated proposals taught us about proposal fatigue," "Deploying self-governance on a $6/mo VPS." Pieces 1 and 4 use DB data already present; each ends with the existing two-CTA strip.
- **Product Hunt: defer.** Governance tools don't chart on PH; the algorithm needs a warm verified-account network. Revisit after 500+ GitHub stars and a successful HN launch; tag under Developer Tools / Open Source.
- **Reddit r/selfhosted (758k) and r/cooperatives (7.3k):** moderate fit. If posting, lead r/selfhosted with the ops story ("one 8GB VPS, no Redis, no Kafka, local Ollama"), not governance theory. Don't target general Discord servers — Kibbutznik fits the active governance core (50-200-person Fediverse co-ops), not the 20-of-500-active passive server.

---

## Go-to-Market Plan

### Next 7 days (recover + sharpen)
1. **Day 1-2:** Email hn@ycombinator.com (P0 #1). Do NOT touch a second account (P0 #2).
2. **Day 1-2:** Start the HN account warmup — 2-3 substantive comments on unrelated tech threads (P0 #3, #12).
3. **Day 2-3:** Ops safety: verify `KBZ_AUTH_DEV_EXPOSE_MAGIC_LINK` unset, set OpenRouter $100 cap (P2).
4. **Day 3-5:** Pick the canonical pitch ("Pulse, not politics. Members, not managers.") and propagate to title/README/OG/HN draft (P0 #5). Fix the homepage canonical: route the psychedelic page, fix og:url (P0 #4).
5. **Day 4-5:** Fix the GitHub repo (description, topics, social preview) (P1 #11). Run the SEO scaffolding batch (P1 #10).
6. **Day 5-7:** Reframe the hero around tech worker co-ops; promote the viewer CTA; fix the "real communities" copy; add the viewer "Start your own" button (P0 #6, #8; P1 #14, #15).
7. **Day 6-7:** Submit to Metagov Gateway; email social.coop's Community Working Group (P0 #9; P1 #17).

### Next 30 days (build momentum + audience)
- **Weeks 1-4:** Sustain HN commenting toward 100+ karma (P1 #12). Join Metagov Slack and participate; secure a Lobsters invite; stand up Fosstodon + social.coop accounts (P1 #17).
- **Weeks 2-3:** Post the viewer link to USFWC + PCC Slacks with the "3 co-ops, 30-day pilot" ask (P0 #9). Email PCC for a newsletter mention.
- **Weeks 2-4:** Publish the "DAO governance without tokens" post and the pulse-mechanic technical essay; submit the latter to Lobsters once the invite/window clears (P1 #17; P2). Upload + embed the promo video.
- **Week 3-4:** Complete the Hetzner upgrade and a load test a full week ahead of retry (P2). Grow GitHub stars to 50+ via these channels (P1 #11).
- **Throughout:** Land the Actions/hierarchy hook, add the PolicyKit/Tally FAQ answers, add the newsletter signup to welcome.html (P1 #13, #16; P2).

### The relaunch move (~week 5-6)
With the account at 200+ karma and >4 weeks old, a repo at 50+ stars with full metadata, a published blog trail, a promo video, and 1-3 real co-op pilots underway: **resubmit the unchanged HN_todo.md draft on a Sunday ~12:00 UTC.** In the prior week, individually message 5-8 genuine contacts for honest reactions (not upvotes). Founder blocks 4 hours post-submission to reply to every top-level comment within 30 minutes — the strongest organic signal HN's algorithm reads. Lead the post with governance mechanics (pulse, hierarchical Actions, no admins), keep AI as the closing hook, drop the live viewer link in the first reply, and cite the Tally shutdown when "why no blockchain" comes up. If the second-chance pool was granted earlier, that supersedes a fresh submission.
