# Relaunch collateral — ready to use

Companion to `docs/audits/2026-05-29-marketing.md` (the full GTM plan). This
file is the *paste-ready* stuff: social threads, the channel checklist with
exact asks, and the canonical-pitch decision. Nothing here has been posted —
it's drafted for Uri to review and send.

> **One decision needed from Uri first:** pick the canonical one-liner and
> we propagate it everywhere (title / README / OG / social bios). Candidates:
> 1. *"A pulse-based direct democratic engine"* (current hero — mechanism-first)
> 2. *"Pulse, not politics. Members, not managers."* (audit's pick — punchier)
> 3. *"Group decisions without admins or drama."* (benefit-first, plain)
> My rec: keep **#1** as the formal tagline, use **#2** as the social/HN hook.

---

## The relaunch sequence (condensed from the audit)

The HN flag was about account credibility, not the project. So the relaunch
is gated on warming the account + building visible momentum, then re-posting.

**Now → week 1 (recover + sharpen):** ✅ several already shipped by Claude —
repo description/topics/homepage set, SEO scaffolding live, design
credibility fixes live, security hardened. Remaining human items: keep the
mods email thread warm; pick the canonical pitch (above).

**Weeks 1–4 (warm the account + audience):**
- HN account: 5–10 substantive comments/week on unrelated tech threads
  (AI agents, co-ops, distributed systems, FastAPI/Postgres). Target 100+
  karma by week 4, 200+ by week 6. **Only Uri can do this — it must be
  genuine.**
- Join the Metagov Slack; participate 2–3 weeks; then propose a Wednesday
  seminar + drop the viewer link in show-and-tell.
- Stand up Fosstodon + social.coop accounts (assets in `branding/`); post the
  thread below.
- Grow GitHub stars to 50+ through these channels.

**Weeks 2–3 (pilots):** post the viewer link to USFWC + Platform
Cooperativism Consortium Slacks with the "3 co-ops, 30-day pilot" ask below.

**Week 5–6 (relaunch):** with the account at 200+ karma, repo at 50+ stars,
a blog trail, and 1–3 pilots underway — resubmit the HN post (Sunday ~12:00
UTC). Message 5–8 genuine contacts the week before for honest reactions (not
upvotes). Block 4h to reply to every top-level comment within 30 min.

---

## Social launch thread (Fosstodon / Mastodon / Bluesky)

*Post when the account/repo are warm — not before. 3 posts.*

**1/**
> Most group-chat tools never actually *decide* anything. I built Kibbutznik:
> communities that govern themselves by a shared "pulse" — a periodic
> heartbeat where whatever has enough support becomes the rule. Open source,
> no admins, no tokens. 🧵

**2/**
> Two things I haven't seen combined elsewhere: (1) *hierarchical Actions* —
> any group can spin off a sub-community with its own pulse that commits work
> back to the parent; (2) *nothing is privileged* — even the support
> threshold is just a variable the group can vote to change.

**3/**
> There's a live instance running on AI members you can watch decide in real
> time (press play): https://kibbutznik.org/kbz/viewer/ — and you can run your
> own at https://kibbutznik.org . Python/FastAPI/Postgres, MIT.
> I'd love to hear what you'd want it to do.

## Lobsters essay (technical, NOT a launch) — outline

*Needs an invite + the 70-day account window. Title: "The pulse: why
governance tools fail without a shared deciding moment."*
- The failure mode: forums/chats collect opinion but have no commit point;
  proposals drift, threads die, loudest voice wins.
- The pulse as a periodic barrier; how OutThere→OnTheAir→Accepted maps to it.
- Statements + variables as an editable rulebook (incl. the threshold itself).
- Hierarchical Actions: recursion as org structure.
- Honest limits: tested at ~30 members; scaling is open research.
- Stack + deploy footnote (one 8GB VPS, no Redis/Kafka/k8s).

---

## Channel checklist (exact asks)

| Channel | Fit | The ask |
|---|---|---|
| **Metagov Slack** (~1,700 governance practitioners) | highest | Participate 2-3wk → propose a seminar + share viewer in show-and-tell |
| **Metagov Gateway** (tool registry) | high, self-serve | Submit Kibbutznik as a tool — do this now, it's low-effort |
| **social.coop** (Mastodon co-op, runs on Loomio) | high | Email their Community Working Group: 3-sentence demo offer |
| **USFWC + Platform Cooperativism Slacks** | high | "Looking for 3 co-ops for a 30-day pilot + honest feedback" |
| **Fosstodon** (~49k FOSS) | good | The 3-post thread above |
| **Lobsters** | good (technical) | The essay above — needs an invite first |
| **r/selfhosted** (758k) | moderate | Lead with the ops story (8GB VPS, no Redis/Kafka, local Ollama option) |
| **Product Hunt** | defer | Governance tools don't chart; revisit after 500+ stars |

---

## Content ideas (each ends with the existing two-CTA strip)

1. **"We ran a 24/7 AI parliament for 30 days — here's what it decided."**
   (uses real sim DB data; the most shareable.)
2. **"The pulse: why governance tools fail without a shared deciding moment."**
   (the Lobsters essay; the conceptual flagship.)
3. **"DAO governance without tokens"** — map the primitives onto DAO
   vocabulary; post to RadicalxChange / Metagov, not the landing page.
4. **"What 200+ simulated proposals taught us about proposal fatigue."**
5. **"Self-governance on a $6/mo VPS"** — the ops/self-hosting angle for
   r/selfhosted and HN's infra-curious crowd.
