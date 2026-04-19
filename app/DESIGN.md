# Kibbutznik — Product Design

> A pulse-based-democracy tool for real human communities. Uses the same
> governance primitives as the AI simulation (communities, proposals,
> supports, pulses, statements, artifacts) but wrapped in a human-first
> interface with accounts, invites, notifications, and trust.

Working name: **Kibbutznik**. We can rename later.

Lives in `/app/` at the repo root and is served statically at
`/app/` via nginx. The backend is the same FastAPI app as the
simulation — shared schema, shared endpoints, shared database.

---

## What humans can do

An MVP product lets a real person:

1. **Register** with an email address (magic-link, no passwords).
2. **Create a Kibbutz** — name it, describe its purpose, set the
   initial governance variables (or accept sensible defaults), write
   the initial statements ("our rules").
3. **Browse / apply** to existing public kibbutzim. Applying files a
   Membership proposal; existing members vote via the pulse system.
4. **Invite people** to a kibbutz they're a member of. The recipient
   gets an email with a one-shot link that auto-files a Membership
   proposal.
5. **Participate** — read active proposals, support them, comment,
   propose new statements/rules, edit artifacts, push the pulse.
6. **Stay in touch** — receive email digests when pulses fire, when
   proposals they backed are accepted/rejected, when they're @mentioned.
7. **Leave** — quit a kibbutz, or be thrown out by vote.

Everything above is governed by the existing pulse machinery. There
is no admin role — everything is proposal-gated.

---

## Directory layout

```
app/
├── DESIGN.md              # this file
├── index.html             # SPA entry; loads React + Babel + app.js
├── app.js                 # React app: routes, state, API calls
├── style.css              # product-specific theme
└── assets/                # logos, email templates, screenshots
```

Same stack as the simulation viewer (React via CDN + Babel Standalone,
no build step, no npm) so we can ship fast and keep the codebase
approachable. Promote to Vite if/when it stops being fun.

---

## URL map

Root is served at `https://kibbutznik.org/app/`.

| Path | Screen |
|---|---|
| `/app/` | Marketing + "log in / sign up" |
| `/app/#/dashboard` | Post-login home: my kibbutzim, pending invites, pending Membership proposals |
| `/app/#/kibbutz/new` | Create a new kibbutz |
| `/app/#/kibbutz/:id` | Tabbed kibbutz view: Feed, Proposals, Statements, Members, Artifacts |
| `/app/#/kibbutz/:id/propose` | New proposal form (type-aware) |
| `/app/#/browse` | Browse public kibbutzim |
| `/app/#/invite/:code` | Accept an invitation |
| `/app/#/profile` | My email, display name, leave kibbutzim, revoke sessions |

Hash routes (`/#/…`) because we're a pure SPA and don't want to
deal with HTML5 history pushState + nginx fallback rules for an MVP.

---

## Backend contract

The product speaks only to endpoints that already exist on the shared
FastAPI app (plus a few small additions this plan calls out). Nothing
is a special-case backend path for "the product" — everything is a
first-class route.

### Already shipped (from Track C1)

- `POST /auth/request-magic-link`     — start magic-link flow
- `GET  /auth/verify?token=…`         — consume link, set session cookie
- `POST /auth/logout`
- `GET  /auth/me`
- `POST /communities/{id}/invites`    — logged-in members generate invites
- `GET  /invites/{code}`              — preview
- `POST /invites/claim`               — claim (files Membership proposal)
- `POST /communities`                 — create a kibbutz
- `POST /communities/{id}/proposals`  — file any proposal type
- `POST /proposals/{id}/support`
- `POST /entities/{type}/{id}/comments`
- `POST /communities/{id}/pulses/support`
- `GET  /metrics/community/{id}`      — governance health dashboard

### Needed additions (MVP Phase B)

These are thin and reuse existing services. Each is a one-route delta.

| Route | Purpose |
|---|---|
| `GET /communities?public=true&q=…` | Browse / search public kibbutzim |
| `GET /users/me/memberships`        | List communities I'm in (via members join) |
| `GET /users/me/pending-invites`    | Open invite codes directed at my email |
| `GET /users/me/pending-proposals`  | Membership proposals I authored that are still in flight |
| `PATCH /users/me`                  | Update display name, about |
| `POST /communities/{id}/leave`     | Set my Member.status = THROWN_OUT voluntarily |
| `GET /communities/{id}/feed`       | Unified activity feed: proposals + pulses + comments, latest-first |
| `POST /proposals/{id}/withdraw`    | Author cancels their own proposal before quorum |

Each can be shipped in ~1 hour; I'll batch them in a single commit
during Phase B.

### Security tightening (promoted from C1 follow-up)

Existing write endpoints accept `user_id` in the body. Agents need
this because they have no session cookie. For human-originated
requests we need to enforce `body.user_id == session.user_id` when a
session cookie is present. Middleware pattern:

```
if request.has_cookie(session):
    session_user = resolve(session)
    if request.body.user_id and request.body.user_id != session_user.id:
        abort(403)
```

Agents (no cookie) are unaffected. Humans can't impersonate anyone.

---

## Contact channels

| Medium | Used for | Cost | Status |
|---|---|---|---|
| **Transactional email** | Magic links, invites, proposal-outcome digests, @mentions | Resend free tier (3k/mo) | ✅ wired in `email_service.py` |
| **In-app notification bell** | Everything above, live in the product UI | Free (runs off existing DB + WS) | 🔜 Phase B |
| **Weekly digest email** | "Here's what happened in your kibbutzim this week" | Resend free tier | 🔜 Phase C |
| **Browser push (Web Push API)** | Real-time nudges when a pulse you care about fires | Free (VAPID keys, no vendor) | 🔜 Phase C |
| **Telegram bot** | Optional subscription for users who prefer chat over email | Free via BotFather | 📋 future |
| **SMS (Twilio)** | 2FA / critical alerts | $$ per message | 📋 future — only if demand |
| **Webhooks** | Advanced users: pipe events to their own tooling | Free | 📋 future |

### Email provider choice: **Resend**

Why not the alternatives:

- **SendGrid** — free tier but well-known deliverability issues; many
  free-tier accounts get flagged as spam.
- **AWS SES** — cheapest at scale ($0.10 per 1k emails) but requires
  AWS setup + manual reputation bootstrap (sandbox → out-of-sandbox
  ticket). Not MVP-friendly.
- **Postmark** — best-in-class deliverability, $15/mo for 10k emails.
  Overkill for a first 100-user launch.
- **Mailgun** — 5k/mo free for 3 months, then $35/mo. No permanent
  free tier.

Resend wins on: permanent free tier covering a whole early cohort,
modern HTTP API, clean SDK, strong deliverability, simple DNS setup
(SPF + DKIM records on our custom domain).

### Setup checklist for production send

1. Sign up at [resend.com](https://resend.com) → grab API key.
2. Add DNS records Resend prescribes (SPF + DKIM) for
   `mail.kibbutznik.org` subdomain.
3. Set env on the server: `KBZ_EMAIL_BACKEND=resend`,
   `KBZ_RESEND_API_KEY=...`, `KBZ_EMAIL_FROM="Kibbutznik <hello@kibbutznik.org>"`.
4. Restart `kbz.service` once.
5. Verify by hitting `/auth/request-magic-link` with a real address.

Until step 1 happens we run `KBZ_EMAIL_BACKEND=log`, which captures
emails in memory so dev + tests work without spending anything.

---

## Roadmap

### Phase A — mechanics ✅ (this commit)
- Remove login UI from simulation viewer
- 500-event auto-pause (credit safety net)
- `EmailService` abstraction + Resend backend
- `app/` directory + DESIGN.md + starter index/landing
- Nginx serves `/app/` statically

### Phase B — MVP product pages (next)
- `index.html` landing → register form
- Dashboard (my kibbutzim + pending state)
- Create-a-kibbutz form
- Kibbutz view: Feed + Proposals + Members + Invite
- Propose/Support/Comment flows for humans
- Invite-claim page (`/#/invite/:code`)
- Profile / logout
- New backend endpoints listed above

### Phase C — polish
- Mobile CSS pass (tables → cards)
- In-app notification bell with WS subscribe
- Weekly digest email
- Browser push
- Rate limiting (3 in-flight proposals per round per human)
- `body.user_id == session.user_id` enforcement

### Phase D — scale
- Telegram bot
- Public kibbutz discovery (search, tags, featured)
- Community analytics (reuses `/metrics/community`)
- Moderation tools (community-level, not platform-level)

---

## What we are NOT building

- Passwords. Only magic links. Simpler, safer, no "forgot my password" flow.
- Admin-only features. Governance handles moderation via ThrowOut.
- Payments. Economy layer (Track B from the roadmap) is still deferred.
- Mobile apps. Web only; mobile-responsive CSS is enough for MVP.
- OAuth providers. Email is enough for the first 100 users.

---

## Open questions for the user

1. **Product name** — "Kibbutznik" is a placeholder. Should we pick a
   final name before Phase B starts? Affects the logo, the email
   sender name ("<NAME> Kibbutz <hello@…>"), and marketing copy.
2. **Domain for email** — use `kibbutznik.org` (current domain) or a
   dedicated transactional subdomain like `mail.kibbutznik.org`? Resend
   recommends the subdomain so transactional sends don't share
   reputation with web traffic.
3. **Public vs private kibbutzim** — MVP assumes all kibbutzim are
   discoverable. Should private (invite-only) be a day-one feature?
4. **Seed kibbutzim** — for the launch, do we want to seed a few
   demonstration communities (e.g., "KBZ Founders", "Indie Hackers NYC")
   so new users have something to browse into? Or stay empty until
   someone creates one?
