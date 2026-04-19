---
name: kibbutznik
description: Participate in Kibbutznik communities — propose rules, support proposals, comment, and push pulses on behalf of a logged-in user. Use when the user asks about their Kibbutznik communities, wants to act in a kibbutz, asks "what's pending in my X kibbutz", or wants to create/support proposals. Requires KIBBUTZNIK_API_TOKEN in env (created at kibbutznik.org/app).
---

# Kibbutznik

Kibbutznik is a pulse-based direct-democracy platform. Communities
(kibbutzim) vote on proposals in rounds. Nothing happens instantly —
proposals gather support, then a "pulse" fires and all proposals that
crossed their threshold get accepted, rest get rejected or aged out.

This skill lets you act in Kibbutznik AS the currently-authenticated
user. It does NOT turn you into a bot — you're a tool the user drives.

## Setup

Before using this skill the user needs:
1. An account at https://kibbutznik.org/app (magic-link sign-in, no password)
2. An API token: Profile → API tokens → Create token
3. The token set as `KIBBUTZNIK_API_TOKEN` in your shell env

Prefer using the MCP server (`kibbutznik-mcp`) if available — it gives
you typed tools. This markdown skill is a fallback for hosts that
don't speak MCP.

## Core concepts (cheat sheet)

- **Kibbutz** = community. Has members, statements (rules), proposals.
- **Proposal** moves through statuses: `Draft` → `OutThere` (gathering
  support) → `OnTheAir` (hit support threshold) → `Accepted` or
  `Rejected` when the pulse fires.
- **Pulse** = the tick that resolves all in-flight proposals. Fires
  when enough members "support the pulse".
- **Proposal types** that matter for a human user:
  `AddStatement` · `RemoveStatement` · `ReplaceStatement` ·
  `ChangeVariable` · `AddAction` · `EndAction` · `JoinAction` ·
  `Membership` · `ThrowOut` · `CreateArtifact` · `EditArtifact` ·
  `DelegateArtifact` · `CommitArtifact` · `RemoveArtifact`.
- Comments are capped at 300 chars — be punchy.

## HTTP API (fallback when MCP isn't available)

Base URL: `https://kibbutznik.org/kbz`
Auth: `Authorization: Bearer $KIBBUTZNIK_API_TOKEN` on every request.

```bash
# What kibbutzim am I in?
curl -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  https://kibbutznik.org/kbz/users/me/memberships

# Browse public kibbutzim (search optional)
curl "https://kibbutznik.org/kbz/communities?q=reading"

# Full snapshot of one kibbutz
CID=...   # community id from the list above
curl -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  https://kibbutznik.org/kbz/communities/$CID/proposals

# Resolve my user_id (needed on writes; the server also verifies
# body.user_id matches your token's user)
curl -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  https://kibbutznik.org/kbz/auth/me

# File a proposal
curl -X POST -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<me>","proposal_type":"AddStatement","proposal_text":"We commit to async-first decisions."}' \
  https://kibbutznik.org/kbz/communities/$CID/proposals
# → {"id": "..."}
# Then submit it so it reaches OutThere:
curl -X PATCH -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  https://kibbutznik.org/kbz/proposals/<proposal_id>/submit

# Support a proposal
curl -X POST -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<me>"}' \
  https://kibbutznik.org/kbz/proposals/<proposal_id>/support

# Comment
curl -X POST -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<me>","comment_text":"Agree — this replaces the awkward sync meeting rule."}' \
  https://kibbutznik.org/kbz/entities/proposal/<proposal_id>/comments

# Push the pulse
curl -X POST -H "Authorization: Bearer $KIBBUTZNIK_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<me>"}' \
  https://kibbutznik.org/kbz/communities/$CID/pulses/support
```

## Good behaviors

- **Read before writing.** Always fetch `/communities/{id}/proposals`
  (or use `get_kibbutz_snapshot` in MCP) before creating — a duplicate
  proposal will be 409'd server-side.
- **Quote, don't paraphrase.** If commenting on someone's proposal,
  anchor your comment on a literal phrase from their text.
- **Short comments.** 50 words max; server truncates at 300 chars
  anyway.
- **Support the pulse.** If there are in-flight proposals and you
  agree with the pending direction, `support_pulse` after your other
  actions — otherwise nothing advances.
- **One opinion per proposal.** Server blocks a second comment from
  you on the same proposal; plan your comment before posting.

## Don't do

- Don't impersonate — every write is signed as YOUR user_id by the
  server's auth layer. Even if you set body.user_id to someone else,
  the server rejects with 403.
- Don't flood. The pulse is the natural rate-limiter; one meaningful
  action per round per community is the right pace.
- Don't invent structure. Proposal types, variables, and statements
  are enumerated in the API — ask before making up names.

## Discovery

`openapi.json` lives at https://kibbutznik.org/kbz/openapi.json — fetch
it if you need the full schema for a type you don't see here.
