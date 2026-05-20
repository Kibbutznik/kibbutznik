# OPS — Kibbutznik production runbook

A short, copy-pasteable guide for operating the live system. Aimed at me
six months from now or a teammate who needs to keep the lights on while
I'm asleep.

---

## Prod basics

- **Host:** Hetzner VPS at `157.180.29.140`
- **Domain:** https://kibbutznik.org
- **Plan:** CCX13 (8GB / 4 vCPU, dedicated) — upgraded from CX22 for the HN launch
- **DB:** Postgres 14 on the same box, socket-only, named `kbz`
- **App:** systemd unit `kbz.service` runs `agents.run_with_viewer`
- **Reverse proxy:** nginx (config in `deploy/nginx/kbz.conf`, deployed to `/etc/nginx/sites-enabled/kbz`)

## Deploy

Pushing to `main` on the bare repo at the host triggers a post-receive
hook that pulls, migrates, restarts:

```bash
git push server main   # remote: ssh://157.180.29.140/opt/kbz-repo.git
```

The hook lives at `/opt/kbz-repo.git/hooks/post-receive` (14 lines). It:
1. `git checkout -f main` into `/opt/kbz`
2. `alembic upgrade head`
3. `systemctl restart kbz`

**Nginx is NOT redeployed by the hook.** If you change anything under
`deploy/nginx/`, sync it manually:

```bash
ssh root@157.180.29.140 'cat > /etc/nginx/sites-enabled/kbz' < deploy/nginx/kbz.conf
ssh root@157.180.29.140 'nginx -t && systemctl reload nginx'
```

## Rollback

If a deploy breaks prod, the cleanest move is to push the previous commit
back as `main`. Test the procedure first in a non-emergency window.

```bash
# Look at recent commits
git log --oneline -5

# Roll back to the previous commit (the deploy hook re-runs)
git push server main~1:main --force-with-lease

# Verify
curl -sf https://kibbutznik.org/healthz | jq
ssh root@157.180.29.140 'journalctl -u kbz --since "1 min ago" | tail -40'
```

Do NOT skip the `--force-with-lease`. Avoid plain `--force`.

If the bad commit is already on the prev one, jump further back:
`git push server main~2:main --force-with-lease`.

## Disable the simulation (without taking the site down)

The simulation runs inside the same uvicorn process. Stopping `kbz.service`
takes both down. To stop ONLY the sim (e.g. it's racking up OpenRouter
charges in a loop):

```bash
# Easiest emergency move: env-disable the orchestrator and restart
ssh root@157.180.29.140 'echo KBZ_AGENTS_ENABLED=0 >> /etc/kbz/env && systemctl restart kbz'
```

`KBZ_AGENTS_ENABLED=0` makes `run_with_viewer.py` skip orchestrator
startup — the API + viewer continue serving normally. To re-enable:
remove the line and restart.

## Logs

```bash
# Live tail
ssh root@157.180.29.140 journalctl -u kbz -f

# Errors only, last hour
ssh root@157.180.29.140 'journalctl -u kbz --since "1 hour ago" | grep -i error'

# Nginx access log (HN traffic curve)
ssh root@157.180.29.140 'tail -F /var/log/nginx/access.log'
```

## Health checks

```bash
# Endpoint health
curl -sf https://kibbutznik.org/healthz
curl -sf https://kibbutznik.org/kbz/highlights | jq '. | length'

# Process + worker count
ssh root@157.180.29.140 'ps auxww | grep uvicorn | grep -v grep'

# DB connection count
ssh root@157.180.29.140 'sudo -u postgres psql kbz -c "select count(*) from pg_stat_activity where datname=\"kbz\";"'
```

## OpenRouter spend ceiling

OpenRouter dashboard: https://openrouter.ai/credits

Set a monthly spend cap of **$100** (≈ 3× projected steady-state) before
launch. If the cap trips mid-launch, the sim degrades but the human
product is unaffected — bots stop acting; humans keep voting.

## Vertical scale up / down (Hetzner)

Hetzner Console → server → **Rescale** → pick plan → reboot.

- **Up:** CX22 → CCX13 → CCX23 (online before launch). 5 min downtime.
- **Down:** CCX13 → CX22 if the launch window is over and traffic is back to baseline. Same 5 min downtime.

After rescale verify: `free -h && nproc` shows the new specs, `systemctl status kbz` is active, the sim has resumed (look for `OrchestratorTick` lines in the journal).

## Closing the cookieless-impersonation hole (KBZ_AGENT_API_SECRET)

By default any cookieless caller is trusted to act as the `user_id` in a
request body — this is load-bearing for the simulation (one process acts
as many bot users) but it means an anonymous internet caller could POST
`{"user_id": "<victim>"}` and impersonate any user. The fix is gated on a
shared secret that is **disabled until you set it**:

1. Generate a high-entropy secret: `openssl rand -hex 32`
2. Set it in the prod env (same file as the DB creds) for BOTH the API
   and the sim — they're the same process, so one var covers both:
   ```
   KBZ_AGENT_API_SECRET=<the hex string>
   ```
3. Restart: `systemctl restart kbz`. Now cookieless writes without the
   `X-KBZ-Agent-Secret: <secret>` header get 401; the sim client reads
   the same env var and sends the header automatically.
4. Defense-in-depth: have nginx strip any client-supplied header on the
   public path so a leaked secret can't be replayed from the internet:
   ```
   proxy_set_header X-KBZ-Agent-Secret "";
   ```
   (in the `location /kbz/` block) then `nginx -t && systemctl reload nginx`.

Verify: a cookieless `curl -X POST https://kibbutznik.org/kbz/communities
-d '{"name":"x","founder_user_id":"<any-uuid>"}'` returns 401; the live
sim keeps producing events (it carries the header).

## Pre-launch checklist (T-48h to T+0)

- [ ] Hetzner upgrade to CCX13 done, 24h burn-in clean
- [ ] OpenRouter $100 monthly cap set
- [ ] `KBZ_AUTH_DEV_EXPOSE_MAGIC_LINK` is NOT in the prod env (link must be `null` in the magic-link response)
- [ ] `KBZ_AGENT_API_SECRET` set in prod env + nginx strips the public header (see section above) + sim still produces events after restart
- [ ] `scripts/loadtest.sh` run against prod from non-prod box, p99 ≤ 500ms
- [ ] OPS.md rollback procedure tested on a no-op commit
- [ ] `HN_todo.md` reviewed by a friend who hasn't read the codebase
- [ ] Launch window blocked on calendar (4h, comment-engagement)

## Common emergencies

| Symptom | Likely cause | First move |
|---|---|---|
| Site returns 502 | uvicorn dead | `systemctl restart kbz` |
| Site slow, journal shows pool exhaustion | Too many concurrent slow queries | Check `pg_stat_activity` for long-running queries; if needed, restart kbz |
| OpenRouter spend spiking | Sim restart loop | `KBZ_AGENTS_ENABLED=0` and restart, then debug |
| 500s on `/artifact/<id>` only | Cache busted on a single row | `systemctl restart kbz` — clears the in-process cache |
| Browse page empty | Visibility filter rejected everything | Visit `/communities?include_dead=true` to verify; check that AI Kibbutz Variable `Visibility=public` |
