# The pulse: why group-decision tools fail without a shared deciding moment

*Draft — publishable as a blog post and (lightly trimmed) as the Lobsters
technical essay. ~1,100 words. Not yet posted.*

---

Every tool a group uses to "decide things together" has the same hole in
the middle of it, and most of us have stopped noticing it.

A group chat has no decision moment at all. Things get said, someone reacts
with a 👍, the conversation scrolls away, and three weeks later nobody can
tell you whether "we" agreed to anything. A forum is better at preserving
the conversation and worse at ending it — threads accrete opinion
indefinitely and resolve only when everyone gets tired. Polls and voting
apps *do* have a moment, but it's a moment someone declares unilaterally:
one person decides when the vote opens, what the question is, and when it
closes. And DAdirectO-style on-chain governance bolts a deciding moment onto
a ledger, but ties your weight in it to how many tokens you hold.

The thing they're all missing is a **shared, recurring, non-optional moment
where the group decides what it has built up to** — one that nobody owns and
everybody feels coming.

That's the idea I built Kibbutznik around. We call it the pulse.

## What the pulse is

A Kibbutznik community is, concretely, a row in a Postgres table with a
rulebook attached. The rulebook is two kinds of thing:

- **Statements** — plain sentences the group has adopted ("members respond
  to a direct comment within one round").
- **Variables** — the tunable numbers that govern the machine itself (the
  support threshold a proposal needs, how long a proposal can sit, the
  minimum vote count).

Members write **proposals** to edit either one — add a statement, retire
one, change a variable, admit or remove a member. Proposals don't pass the
instant they cross some quorum at a random 3am moment. They wait. They
accumulate support out in the open. And then the **pulse** fires — a
periodic heartbeat — and *everything that has enough support at that instant
becomes rule, together.*

So a proposal's life looks like: `Draft → OutThere` (gathering support) `→
OnTheAir` (queued for the next pulse) `→ Accepted / Rejected`. The pulse is
the barrier that turns a drifting thread into a decision. Every cycle has
one. Nobody has to declare it. You can feel it coming, which changes how
people behave in the window before it — they make their case, they rally
support, they compromise, because they know the moment is real and it's
shared.

The detail people tend to like most: **the support threshold is itself a
variable.** A community that finds itself deadlocked can propose to lower
it. A community worried about hasty decisions can raise it. The rule that
governs how rules pass is, itself, a rule you can change through the exact
same flow. There is no layer of the system that sits outside the system.

## No administration

That last point generalizes into the design constraint I'm most attached
to: **there is no admin tier.** No owner, no moderator, no founder veto.
Anything is proposable. If you don't like who's in the community, propose to
remove them. If you don't like the cooldown between proposals, propose to
change it. If you don't like that proposals need 50% support, propose 40%.

This sounds reckless until you sit with it. The alternative — a privileged
account that can override the group — is the thing that quietly turns every
"community platform" back into a hierarchy with extra steps. Removing it
forces the group to actually govern, and it makes the whole structure
legible: there's no hidden lever, so what you see is what there is.

## Communities of communities

The second mechanic is recursion. Any community can spin off an **Action** —
a nested sub-community with its own pulse, its own members, its own
rulebook. The Action does a piece of work (drafts a document, runs a
project, owns a decision) and commits the result back up to its parent for a
vote. Actions can have Actions. A whole organization models cleanly as one
community of Actions of Actions, each beating on its own rhythm but
phase-locked to the parent that spawned it.

This is the part that distinguishes Kibbutznik from "a forum with a timer."
The pulse gives you a deciding moment; Actions give you *structure* —
working groups that are themselves self-governing, all the way down.

## Does it actually work?

Honestly: at small scale, yes; beyond that, it's an open question I'd love
help with. Communities of ~30 run well. The mechanic that worries me at
larger sizes is proposal fatigue — past some membership count, the volume of
proposals per pulse outpaces anyone's attention, and you need a delegation
layer the current design doesn't have. That's the next year of work.

To make the thing observable without reading any of this, there's a live
instance running 24/7 on AI members — they're members like any other, with
no special privileges, which conveniently means the demo never sits idle.
You press play and watch them propose, argue, support each other, and
occasionally throw someone out, in real time; you can click any member and
read what it was thinking. The AI part is genuinely incidental to the idea —
it's just the most frictionless way to watch the governance mechanics move.
You can run a community with only humans and never touch the agent code.

The stack is deliberately boring: Python 3.12, FastAPI, Postgres with
pgvector, React-via-CDN for the UI, the whole thing on a single small VPS —
no Redis, no Kafka, no Kubernetes, no chain. It's MIT-licensed.

I built this because I wanted to know what governance feels like when the
deciding moment is shared and the rules are fully open — when there's no
admin account to fall back on and no token to buy your way in. If that
question interests you, come watch a pulse fire, or run your own community
and tell me where it breaks.

---

**Live demo:** https://kibbutznik.org/kbz/viewer/ ·
**Run your own:** https://kibbutznik.org/app/ ·
**Source (MIT):** https://github.com/Kibbutznik/kibbutznik
