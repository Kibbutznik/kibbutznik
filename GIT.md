# KBZ Git Sync — Dev Machine Cheatsheet

The bare repo at `/opt/kbz-repo.git` on the server is your central hub.
Pushing to it automatically deploys and restarts the KBZ service.

```
[this machine]  ──push/pull──▶  /opt/kbz-repo.git  ◀──push/pull──  /opt/kbz (server workdir)
```

---

## Scenario 1 — Deploy local changes to server

```bash
cd /Users/uriee/claude/kbz

git add -A
git commit -m "your message"
git push server main
```

The hook runs automatically: installs deps → restarts kbz service.

---

## Scenario 2 — Pull server changes to local

Someone edited code on the server (via code-server) and pushed. Pull it here:

```bash
cd /Users/uriee/claude/kbz

git pull server main
```

If there are conflicts, git will tell you. Resolve them, then commit.

---

## Scenario 3 — Both sides changed (potential conflict)

Always pull before you start working:

```bash
# Before coding locally — sync first:
git pull server main

# ... make your changes ...

git add -A
git commit -m "your message"
git push server main
```

---

## Scenario 4 — Check what's different before pushing

```bash
# See what commits the server has that you don't:
git fetch server
git log HEAD..server/main --oneline

# See what you have that the server doesn't:
git log server/main..HEAD --oneline

# Full diff:
git diff server/main
```

---

## Scenario 5 — Force push (overwrite server with local, nuclear option)

Only use this if you're sure local is correct and want to throw away server changes:

```bash
git push server main --force
```

---

## Remotes

| Remote | URL |
|--------|-----|
| server | ssh://root@157.180.29.140/opt/kbz-repo.git |

Add it if missing:
```bash
git remote add server ssh://root@157.180.29.140/opt/kbz-repo.git
```

---

## Notes

- `config.ini` is gitignored — never committed. Copy it manually if needed.
- Pushing triggers `post-receive` hook: `pip install -e .` + `systemctl restart kbz`
- The KBZ service restarts automatically on every push — brief downtime (~3s)
- To push WITHOUT restarting: edit `/opt/kbz-repo.git/hooks/post-receive` on server
