/*
 * Kibbutznik — Phase B product UI.
 *
 * Hash-routed SPA. No build step. Same backend as the simulation viewer
 * (FastAPI at /kbz/ behind nginx, same in dev via CORS).
 *
 * Routes:
 *   #/              — landing
 *   #/login         — magic-link sign-in
 *   #/dashboard     — my kibbutzim + in-flight applications + sent invites
 *   #/browse        — public kibbutzim (search + apply-to-join)
 *   #/kibbutz/new   — create a new kibbutz
 *   #/kibbutz/:id   — kibbutz view (feed + proposals + members + invite)
 *   #/kibbutz/:id/propose  — new proposal form
 *   #/invite/:code  — accept an invitation
 *   #/profile       — rename, view email, logout
 */

const { useState, useEffect, useCallback, useMemo, useRef } = React;

// ── API base ────────────────────────────────────────────
const API_BASE = (() => {
    const { pathname } = window.location;
    if (pathname.startsWith("/app/")) return "/kbz";    // prod behind nginx
    return "";                                           // local dev
})();

const api = {
    async _fetch(path, opts = {}) {
        const resp = await fetch(API_BASE + path, {
            credentials: "include",
            headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
            ...opts,
        });
        if (resp.status === 204) return null;
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            const msg = body.detail || body.error || `HTTP ${resp.status}`;
            const e = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
            e.status = resp.status;
            throw e;
        }
        return body;
    },
    get(p)        { return this._fetch(p); },
    post(p, body) { return this._fetch(p, { method: "POST",   body: JSON.stringify(body || {}) }); },
    patch(p, body){ return this._fetch(p, { method: "PATCH",  body: JSON.stringify(body || {}) }); },
};

// ── Toast notifications ────────────────────────────────
// Slide-in, auto-dismiss. One host at the root level; emit via
// `toast(msg, kind?)` from anywhere. Kinds: "success" | "error" |
// "info". Replaces every alert() in the app — alerts hijack focus
// and feel like 1999.
const _toastListeners = new Set();
let _toastCounter = 0;

function toast(message, kind = "info", timeoutMs = 4000) {
    const id = ++_toastCounter;
    const item = { id, message, kind, timeoutMs };
    _toastListeners.forEach(fn => fn({ type: "push", item }));
    return id;
}

function ToastHost() {
    const [items, setItems] = useState([]);
    useEffect(() => {
        const handler = (evt) => {
            if (evt.type === "push") {
                setItems(xs => [...xs, evt.item]);
                if (evt.item.timeoutMs > 0) {
                    setTimeout(
                        () => setItems(xs => xs.filter(t => t.id !== evt.item.id)),
                        evt.item.timeoutMs,
                    );
                }
            }
        };
        _toastListeners.add(handler);
        return () => _toastListeners.delete(handler);
    }, []);
    const dismiss = (id) => setItems(xs => xs.filter(t => t.id !== id));

    return (
        <div style={{
            position: "fixed", bottom: "1rem", right: "1rem",
            zIndex: 1000, display: "flex", flexDirection: "column",
            gap: "0.5rem", maxWidth: 380, pointerEvents: "none",
        }}>
            {items.map(t => {
                const palette = {
                    success: { bg: "#e6f7ef", border: "#4ecca3", fg: "#0c6047" },
                    error:   { bg: "#ffe5e9", border: "#c14b57", fg: "#7a1a24" },
                    info:    { bg: "#eef2ff", border: "#6b7fd7", fg: "#2a3566" },
                }[t.kind] || {};
                return (
                    <div key={t.id}
                         onClick={() => dismiss(t.id)}
                         className="toast-slide-in"
                         style={{
                             pointerEvents: "auto", cursor: "pointer",
                             background: palette.bg, color: palette.fg,
                             border: `1px solid ${palette.border}`,
                             padding: "0.6rem 0.9rem", borderRadius: 8,
                             boxShadow: "0 4px 14px rgba(0,0,0,0.08)",
                             fontSize: "0.88rem", lineHeight: 1.35,
                         }}>
                        {t.message}
                    </div>
                );
            })}
            <style>{`
                @keyframes toast-slide-in-kf {
                    from { transform: translateX(120%); opacity: 0; }
                    to   { transform: translateX(0);     opacity: 1; }
                }
                .toast-slide-in { animation: toast-slide-in-kf 0.25s ease-out; }
            `}</style>
        </div>
    );
}


// ── WebSocket live-events hook ──────────────────────────
// Mirrors the simulation viewer's pattern — single connection, feeds
// news ticker + triggers debounced refetches on state-change events.
// Scoped by optional community_id filter so tabs only show their own
// events.
function useLiveEvents({ communityId, onRefresh } = {}) {
    const [events, setEvents] = useState([]);     // rolling buffer, newest first
    const [connected, setConnected] = useState(false);
    const wsRef = useRef(null);
    const refreshTimer = useRef(null);

    useEffect(() => {
        function connect() {
            const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
            const base = API_BASE || "";
            const url = `${proto}//${window.location.host}${base}/ws/events`;
            const ws = new WebSocket(url);
            ws.onopen = () => setConnected(true);
            ws.onclose = () => {
                setConnected(false);
                setTimeout(connect, 3000);  // auto-reconnect
            };
            ws.onerror = () => ws.close();
            ws.onmessage = (e) => {
                try {
                    const evt = JSON.parse(e.data);
                    // Scope by community_id if the caller asked
                    if (communityId && evt.community_id && evt.community_id !== communityId) {
                        return;
                    }
                    setEvents(xs => [evt, ...xs].slice(0, 50));  // keep last 50
                    // Debounced refresh on state-change events
                    const stateChange = new Set([
                        "proposal.created", "proposal.accepted", "proposal.rejected",
                        "support.cast", "support.withdrawn", "pulse.executed",
                        "round.end", "comment.posted",
                    ]);
                    if (onRefresh && stateChange.has(evt.event_type)) {
                        if (refreshTimer.current) clearTimeout(refreshTimer.current);
                        refreshTimer.current = setTimeout(onRefresh, 600);
                    }
                } catch {}
            };
            wsRef.current = ws;
        }
        connect();
        return () => {
            if (refreshTimer.current) clearTimeout(refreshTimer.current);
            if (wsRef.current) wsRef.current.close();
        };
    }, [communityId, onRefresh]);

    return { events, connected };
}

// ── Pulse progress bar + support button ─────────────────
function PulseBar({ communityId, user, imMember, pulses, members, onChanged }) {
    const nextPulse = pulses.find(p => p.status === 0);   // 0 = NEXT per enum
    const activePulse = pulses.find(p => p.status === 1); // 1 = ACTIVE
    const [busy, setBusy] = useState(false);
    const [supporters, setSupporters] = useState([]);
    const [showSupporters, setShowSupporters] = useState(false);

    const p = nextPulse || activePulse;
    const pulseId = p?.id;

    // Fetch current supporters so we can toggle support/withdraw instead
    // of letting the user click a second time and get a 409.
    useEffect(() => {
        if (!pulseId) { setSupporters([]); return; }
        let cancelled = false;
        api.get(`/pulses/${pulseId}/supporters`)
            .then(list => { if (!cancelled) setSupporters(list || []); })
            .catch(() => { if (!cancelled) setSupporters([]); });
        return () => { cancelled = true; };
    }, [pulseId, p?.support_count]);

    const alreadySupported = user && supporters.some(
        s => (s.user_id || s) === user.user_id,
    );

    const support = async () => {
        if (!imMember || !user) return;
        setBusy(true);
        try {
            const r = await api.post(`/communities/${communityId}/pulses/support`, {
                user_id: user.user_id,
            });
            if (r?.pulse_triggered) {
                toast("⚡ Pulse fired — proposals are being decided.", "success");
            } else {
                toast("Support recorded.", "success");
            }
            onChanged?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setBusy(false); }
    };

    const withdraw = async () => {
        if (!imMember || !user) return;
        setBusy(true);
        try {
            await api._fetch(
                `/communities/${communityId}/pulses/support/${user.user_id}`,
                { method: "DELETE" },
            );
            toast("Support withdrawn.", "info");
            onChanged?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setBusy(false); }
    };

    if (!p) return null;

    const pct = Math.min(100, (p.support_count / Math.max(1, p.threshold)) * 100);
    const ready = p.support_count >= p.threshold;

    return (
        <div className="card" style={{
            padding: "0.6rem 0.9rem", marginBottom: "1rem",
            background: ready ? "rgba(78,204,163,0.1)" : "var(--bg-card)",
        }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                    <div className="row" style={{ gap: "0.6rem", alignItems: "baseline" }}>
                        <strong>⚡ Next pulse</strong>
                        <span className="muted" style={{ fontSize: "0.85rem" }}>
                            {p.support_count} / {p.threshold} supporters
                            {ready && " — ready to fire"}
                        </span>
                    </div>
                    <div style={{
                        height: 6, background: "rgba(0,0,0,0.08)",
                        borderRadius: 3, marginTop: 6, overflow: "hidden",
                    }}>
                        <div style={{
                            width: `${pct}%`, height: "100%",
                            background: ready ? "var(--accent)" : "var(--warn)",
                            transition: "width .3s",
                        }} />
                    </div>
                </div>
                {imMember && (
                    alreadySupported ? (
                        <button className="btn" style={{ marginLeft: "0.8rem" }}
                                disabled={busy} onClick={withdraw}
                                title="You've already supported — click to withdraw">
                            {busy ? "…" : "✓ Supported"}
                        </button>
                    ) : (
                        <button className="btn primary" style={{ marginLeft: "0.8rem" }}
                                disabled={busy} onClick={support}>
                            {busy ? "…" : "⚡ Support pulse"}
                        </button>
                    )
                )}
            </div>
            {supporters.length > 0 && (
                <div style={{ marginTop: 6 }}>
                    <button className="btn ghost"
                            onClick={() => setShowSupporters(s => !s)}
                            style={{ fontSize: "0.75rem", padding: "2px 6px" }}>
                        {showSupporters ? "▾" : "▸"} {supporters.length} supporter{supporters.length === 1 ? "" : "s"}
                    </button>
                    {showSupporters && (
                        <div className="row" style={{
                            gap: "0.3rem", flexWrap: "wrap", marginTop: 4,
                        }}>
                            {supporters.map((s, i) => {
                                const uid = s.user_id || s;
                                const m = members.find(mm => mm.user_id === uid);
                                const label = s.display_name || s.user_name
                                    || m?.display_name || m?.user_name
                                    || String(uid).slice(0, 8);
                                return (
                                    <span key={i} className="pill" style={{
                                        background: "var(--accent-soft)", color: "var(--accent)",
                                        fontSize: "0.75rem",
                                    }}>{label}</span>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ── News ticker — live event feed (like the sim viewer) ─
function NewsTicker({ events, communityId }) {
    const [open, setOpen] = useState(false);
    if (events.length === 0) return null;

    const formatEvent = (e) => {
        const t = e.event_type;
        if (t === "pulse.executed") return "⚡ Pulse fired";
        if (t === "proposal.created") return `📝 New ${e.data?.proposal_type || "proposal"}`;
        if (t === "proposal.accepted") return `✅ Proposal accepted`;
        if (t === "proposal.rejected") return `❌ Proposal rejected`;
        if (t === "support.cast") return `👍 Support cast`;
        if (t === "comment.posted") return `💬 New comment`;
        if (t === "round.end") return `🔄 Round ended`;
        return t;
    };

    const latest = events[0];
    return (
        <div style={{
            position: "sticky", top: 0, zIndex: 50,
            background: "var(--bg-card)", borderBottom: "1px solid var(--border)",
            padding: "0.4rem 0.9rem", fontSize: "0.82rem",
        }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div className="row" style={{ gap: "0.5rem", overflow: "hidden" }}>
                    <span style={{
                        background: "var(--accent)", color: "white",
                        fontSize: "0.65rem", padding: "1px 6px", borderRadius: 3,
                        fontWeight: 700, letterSpacing: "0.05em",
                    }}>LIVE</span>
                    <span className="muted" style={{
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                    }}>{formatEvent(latest)} · {new Date(latest.timestamp).toLocaleTimeString()}</span>
                </div>
                <button className="btn ghost" onClick={() => setOpen(!open)}
                        style={{ fontSize: "0.78rem", padding: "0.2rem 0.5rem" }}>
                    {open ? "▲" : `▼ ${events.length} recent`}
                </button>
            </div>
            {open && (
                <div style={{
                    maxHeight: 240, overflow: "auto", marginTop: "0.5rem",
                    fontSize: "0.8rem", fontFamily: "ui-monospace, Menlo, monospace",
                }}>
                    {events.slice(0, 30).map((e, i) => (
                        <div key={i} className="muted" style={{ padding: "0.15rem 0" }}>
                            {new Date(e.timestamp).toLocaleTimeString()} · {formatEvent(e)}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Tiny hash router ────────────────────────────────────
function useHashRoute() {
    const parse = () => {
        const h = window.location.hash.replace(/^#/, "");
        const [path, qs] = h.split("?");
        const segments = (path || "/").split("/").filter(Boolean);
        return { path: "/" + segments.join("/"), segments, query: new URLSearchParams(qs || "") };
    };
    const [route, setRoute] = useState(parse);
    useEffect(() => {
        const onChange = () => setRoute(parse());
        window.addEventListener("hashchange", onChange);
        return () => window.removeEventListener("hashchange", onChange);
    }, []);
    return route;
}
function navigate(hash) { window.location.hash = hash; }

// ── Auth hook ───────────────────────────────────────────
function useAuth() {
    const [user, setUser]   = useState(null);
    const [loaded, setLoaded] = useState(false);
    const refresh = useCallback(async () => {
        try {
            const r = await api.get("/auth/me");
            setUser(r.user || null);
        } catch { setUser(null); }
        finally { setLoaded(true); }
    }, []);
    useEffect(() => { refresh(); }, [refresh]);
    const logout = useCallback(async () => {
        try { await api.post("/auth/logout", {}); } catch {}
        setUser(null);
    }, []);
    return { user, loaded, refresh, logout };
}

// ── Shared UI atoms ─────────────────────────────────────
function Header({ user, onLogout }) {
    return (
        <header className="app-header">
            <a href="#/" className="brand">Kibbutznik</a>
            <div className="row">
                {user ? (
                    <>
                        <a href="#/dashboard" className="btn ghost">Dashboard</a>
                        <a href="#/browse" className="btn ghost">Browse</a>
                        <a href="#/skills" className="btn ghost">Skills</a>
                        <a href="#/profile" className="btn ghost">👤 {user.user_name}</a>
                        <button className="btn ghost" onClick={onLogout}>Log out</button>
                    </>
                ) : (
                    <>
                        <a href="#/skills" className="btn ghost">Skills</a>
                        <a href="#/login" className="btn primary">Sign in</a>
                    </>
                )}
            </div>
        </header>
    );
}

function ErrorBanner({ error }) {
    if (!error) return null;
    return <p style={{ color: "var(--danger)", marginTop: "0.8rem" }}>{error}</p>;
}

function Empty({ title, children }) {
    return (
        <div className="card" style={{ textAlign: "center", padding: "2rem" }}>
            <div className="bold" style={{ marginBottom: "0.4rem" }}>{title}</div>
            <div className="muted">{children}</div>
        </div>
    );
}

// ── Landing ─────────────────────────────────────────────
function LandingPage({ user }) {
    return (
        <>
            <section className="hero">
                <h1>Run your community by pulse, not politics.</h1>
                <p>
                    Kibbutznik is a shared-decision tool for groups who want to move together
                    without voting everything to death. Propose, support, pulse, and watch
                    decisions settle. Built on the same pulse engine that runs our AI simulation at{" "}
                    <a href="/kbz/viewer/">kibbutznik.org/kbz/viewer</a>.
                </p>
                <div className="row" style={{ justifyContent: "center" }}>
                    {user
                      ? <a href="#/dashboard" className="btn primary">Open dashboard</a>
                      : <a href="#/login" className="btn primary">Sign up / sign in</a>}
                    <a href="#/browse" className="btn">Browse public kibbutzim</a>
                </div>
                {!user && (
                    <div className="muted" style={{ marginTop: "0.8rem", fontSize: "0.9rem" }}>
                        Free. No passwords. One email is all it takes.
                    </div>
                )}
            </section>
            <div className="container">
                <div className="features">
                    <div className="feature">
                        <div className="feature-title">No passwords</div>
                        <div className="muted">Sign in with a magic link. That's it.</div>
                    </div>
                    <div className="feature">
                        <div className="feature-title">Proposal-gated</div>
                        <div className="muted">No admins. Every change is a community decision.</div>
                    </div>
                    <div className="feature">
                        <div className="feature-title">Pulse-driven</div>
                        <div className="muted">Decisions happen in rounds. No decision drift.</div>
                    </div>
                    <div className="feature">
                        <div className="feature-title">Invite-first</div>
                        <div className="muted">Share a link, the community votes on new members.</div>
                    </div>
                </div>
            </div>
        </>
    );
}

// ── Login ───────────────────────────────────────────────
function LoginPage({ onLoggedIn }) {
    const [email, setEmail] = useState("");
    const [remember, setRemember] = useState(() => {
        try { return localStorage.getItem("kbz-remember") !== "false"; }
        catch { return true; }
    });
    const [sending, setSending] = useState(false);
    const [devLink, setDevLink] = useState(null);
    const [sent, setSent] = useState(false);
    const [error, setError] = useState(null);

    const submit = async (e) => {
        e.preventDefault();
        setSending(true); setError(null); setDevLink(null); setSent(false);
        try { localStorage.setItem("kbz-remember", remember ? "true" : "false"); }
        catch {}
        try {
            const r = await api.post("/auth/request-magic-link", { email, remember });
            if (r.link) setDevLink(r.link);
            else setSent(true);
        } catch (err) { setError(err.message); }
        finally { setSending(false); }
    };

    const verify = async () => {
        try {
            await api.get(devLink);
            await onLoggedIn();
            navigate("#/dashboard");
        } catch (err) { setError(err.message); }
    };

    return (
        <div className="container" style={{ maxWidth: 480 }}>
            <div className="card">
                <h2 style={{ marginTop: 0 }}>Sign in or create your account</h2>
                <p className="muted">
                    Enter your email. If you're new, we'll create your Kibbutznik account
                    automatically — no passwords, no forms. Same email tomorrow =
                    same account. One-time sign-in link, valid 15 minutes.
                </p>
                {!devLink && !sent && (
                    <form className="stack" onSubmit={submit}>
                        <input className="input" type="email" required
                            placeholder="you@example.com"
                            value={email} onChange={(e) => setEmail(e.target.value)} />
                        <label className="row" style={{ gap: "0.5rem", fontSize: "0.88rem", cursor: "pointer" }}>
                            <input type="checkbox" checked={remember}
                                   onChange={(e) => setRemember(e.target.checked)} />
                            <span>Remember me on this device (30 days)</span>
                        </label>
                        <button className="btn primary" disabled={sending || !email}>
                            {sending ? "Sending…" : "Send magic link"}
                        </button>
                    </form>
                )}
                {devLink && (
                    <div className="stack">
                        <p className="muted">Dev-mode link (production will email this instead):</p>
                        <button className="btn primary" onClick={verify}>🔑 Use magic link</button>
                    </div>
                )}
                {sent && (
                    <p className="muted">
                        Check your inbox — the link signs you in
                        for {remember ? "30 days" : "1 day"}.
                    </p>
                )}
                <ErrorBanner error={error} />
            </div>
        </div>
    );
}

// ── Dashboard ───────────────────────────────────────────
const NIL_UUID = "00000000-0000-0000-0000-000000000000";

/* Render the user's memberships as trees rooted at each kibbutz.
 * Action-communities (children) render indented under the root they
 * belong to. Works for arbitrary depth — recurses via MembershipNode. */
function MembershipTree({ memberships, stats }) {
    // Group by parent_id → list of children, indexed by id for quick lookup.
    const { byParent, byId, topLevels } = useMemo(() => {
        const byParent = new Map();
        const byId = new Map();
        for (const m of memberships) {
            byId.set(m.community_id, m);
        }
        for (const m of memberships) {
            // A node's "effective parent" in our tree is its parent_id if
            // we are also a member of that parent, otherwise it's a root
            // in our tree (even if the real community has a parent we
            // don't belong to).
            const pid = m.community_parent_id && m.community_parent_id !== NIL_UUID
                && byId.has(m.community_parent_id)
                ? m.community_parent_id : null;
            if (pid) {
                if (!byParent.has(pid)) byParent.set(pid, []);
                byParent.get(pid).push(m);
            }
        }
        const topLevels = memberships.filter(m => {
            const pid = m.community_parent_id;
            return !pid || pid === NIL_UUID || !byId.has(pid);
        });
        // Stable order: newest first (matches API default).
        topLevels.sort((a, b) => new Date(b.joined_at) - new Date(a.joined_at));
        return { byParent, byId, topLevels };
    }, [memberships]);

    return (
        <div className="stack">
            {topLevels.map(m => (
                <MembershipNode key={m.community_id}
                    membership={m} depth={0}
                    byParent={byParent} stats={stats} />
            ))}
        </div>
    );
}

function MembershipNode({ membership: m, depth, byParent, stats }) {
    const s = stats[m.community_id] || { pulses: [], proposals: [] };
    const nextPulse = s.pulses.find(p => p.status === 0);
    const active = s.proposals.filter(
        p => p.proposal_status === "OutThere" || p.proposal_status === "OnTheAir",
    );
    const landed24h = s.proposals.filter(p => {
        if (p.proposal_status !== "Accepted") return false;
        try {
            return (Date.now() - new Date(p.created_at).getTime()) < 86400000;
        } catch { return false; }
    }).length;
    const pulsePct = nextPulse
        ? Math.min(100, (nextPulse.support_count / Math.max(1, nextPulse.threshold)) * 100)
        : 0;
    const pulseReady = nextPulse && nextPulse.support_count >= nextPulse.threshold;
    const children = (byParent.get(m.community_id) || [])
        .slice()
        .sort((a, b) => new Date(b.joined_at) - new Date(a.joined_at));
    const isAction = depth > 0;

    return (
        <div style={{ marginLeft: depth * 20 }}>
            <a href={`#/kibbutz/${m.community_id}`}
                className="card"
                style={{
                    textDecoration: "none", color: "inherit", display: "block",
                    borderLeft: isAction ? "3px solid var(--accent)" : undefined,
                    background: isAction ? "var(--accent-soft)" : undefined,
                }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div style={{ flex: 1 }}>
                        <div className="row" style={{ gap: "0.5rem" }}>
                            <strong>{m.community_name}</strong>
                            <span className="pill">{isAction ? "action" : "member"}</span>
                            {pulseReady && (
                                <span className="pill" style={{ background: "rgba(240,192,64,0.2)", color: "var(--warn)" }}>
                                    ⚡ ready
                                </span>
                            )}
                        </div>
                        <div className="muted" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                            {active.length} active proposal{active.length === 1 ? "" : "s"}
                            {landed24h > 0 && ` · ${landed24h} accepted today`}
                            {" · seniority "}{m.seniority}
                        </div>
                        {nextPulse && (
                            <div style={{ marginTop: 8 }}>
                                <div className="muted" style={{ fontSize: "0.75rem", marginBottom: 2 }}>
                                    Next pulse: {nextPulse.support_count}/{nextPulse.threshold}
                                </div>
                                <div style={{
                                    height: 4, background: "rgba(0,0,0,0.08)",
                                    borderRadius: 2, overflow: "hidden",
                                }}>
                                    <div style={{
                                        width: `${pulsePct}%`, height: "100%",
                                        background: pulseReady ? "var(--accent)" : "var(--warn)",
                                    }} />
                                </div>
                            </div>
                        )}
                    </div>
                    <span className="muted" style={{ marginLeft: "0.5rem" }}>→</span>
                </div>
            </a>
            {children.length > 0 && (
                <div className="stack" style={{ marginTop: "0.6rem" }}>
                    {children.map(c => (
                        <MembershipNode key={c.community_id}
                            membership={c} depth={depth + 1}
                            byParent={byParent} stats={stats} />
                    ))}
                </div>
            )}
        </div>
    );
}

function DashboardPage({ user }) {
    const [memberships, setMemberships] = useState([]);
    const [pendingApps, setPendingApps] = useState([]);
    const [sentInvites, setSentInvites] = useState([]);
    const [bots, setBots] = useState([]);
    const [wallet, setWallet] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    // Per-kibbutz live stats: { community_id: { pulses, proposals, mySupportedIds } }
    const [stats, setStats] = useState({});

    const reload = useCallback(async () => {
        try {
            const [m, a, s, b, w] = await Promise.all([
                api.get("/users/me/memberships"),
                api.get("/users/me/pending-applications"),
                api.get("/users/me/sent-invites"),
                api.get("/users/me/bots"),
                api.get("/users/me/wallet").catch(() => null),
            ]);
            setMemberships(m);
            setPendingApps(a);
            setSentInvites(s);
            setBots(b);
            setWallet(w);
            // Fan out per-kibbutz fetches for live state
            const nextStats = {};
            await Promise.all((m || []).map(async mm => {
                try {
                    const [pulses, proposals] = await Promise.all([
                        api.get(`/communities/${mm.community_id}/pulses`),
                        api.get(`/communities/${mm.community_id}/proposals`),
                    ]);
                    nextStats[mm.community_id] = { pulses, proposals };
                } catch {
                    nextStats[mm.community_id] = { pulses: [], proposals: [] };
                }
            }));
            setStats(nextStats);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { reload(); }, [reload]);

    // Subscribe to ALL events (no community filter) so any activity in
    // any of the user's kibbutzim triggers a silent refresh.
    useLiveEvents({ onRefresh: reload });

    // Aggregate "needs your attention"
    const attention = useMemo(() => {
        let unsupportedCount = 0;
        let readyPulses = 0;
        const activeStatuses = new Set(["OutThere", "OnTheAir"]);
        for (const mm of memberships) {
            const s = stats[mm.community_id];
            if (!s) continue;
            const activeProps = (s.proposals || []).filter(p => activeStatuses.has(p.proposal_status));
            unsupportedCount += activeProps.length;  // conservative: we don't fetch per-proposal supporter lists
            const nextPulse = (s.pulses || []).find(p => p.status === 0);
            if (nextPulse && nextPulse.support_count >= nextPulse.threshold) readyPulses++;
        }
        return { unsupportedCount, readyPulses };
    }, [memberships, stats]);

    return (
        <div className="container">
            <div className="row" style={{ justifyContent: "space-between", marginBottom: "1rem" }}>
                <div>
                    <h2 style={{ margin: 0 }}>Welcome back, {user.user_name}</h2>
                    <div className="muted">{user.email}</div>
                </div>
                <div className="row">
                    <a href="#/browse" className="btn">Browse kibbutzim</a>
                    <a href="#/kibbutz/new" className="btn primary">+ Create kibbutz</a>
                </div>
            </div>
            <ErrorBanner error={error} />

            {wallet && parseFloat(wallet.balance) > 0 && (
                <section style={{ marginBottom: "1.5rem" }}>
                    <div className="card" style={{
                        background: "var(--accent-soft)",
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                    }}>
                        <div>
                            <div className="muted" style={{ fontSize: "0.85rem" }}>💰 My credits</div>
                            <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--accent)" }}>
                                {parseFloat(wallet.balance).toFixed(2)}
                            </div>
                        </div>
                        <div className="muted" style={{ fontSize: "0.82rem", textAlign: "right", maxWidth: 300 }}>
                            Used to pay membership fees when applying to financial kibbutzim.
                            Grows via welcome gift, dividends, and webhook-backed deposits.
                        </div>
                    </div>
                </section>
            )}

            {(attention.unsupportedCount > 0 || attention.readyPulses > 0) && (
                <section style={{ marginBottom: "1.5rem" }}>
                    <div className="card" style={{
                        background: "rgba(240, 192, 64, 0.12)",
                        borderLeft: "3px solid var(--warn)",
                    }}>
                        <div className="bold" style={{ marginBottom: "0.3rem" }}>
                            🔔 Needs your attention
                        </div>
                        <div className="muted" style={{ fontSize: "0.9rem" }}>
                            {attention.readyPulses > 0 && (
                                <div>⚡ {attention.readyPulses} pulse{attention.readyPulses > 1 ? "s" : ""} ready to fire — your support would lock in the pending decisions.</div>
                            )}
                            {attention.unsupportedCount > 0 && (
                                <div>📝 {attention.unsupportedCount} active proposal{attention.unsupportedCount > 1 ? "s" : ""} across your kibbutzim — visit each to vote.</div>
                            )}
                        </div>
                    </div>
                </section>
            )}

            <section style={{ marginBottom: "1.5rem" }}>
                <h3>Your kibbutzim</h3>
                {loading ? <div className="muted">Loading…</div>
                 : memberships.length === 0 ? (
                    <Empty title="You're not a member of any kibbutz yet">
                        Create one above, or <a href="#/browse">apply to join an existing one</a>.
                    </Empty>
                ) : (
                    <MembershipTree memberships={memberships} stats={stats} />
                )}
            </section>

            {bots.length > 0 && (
                <section style={{ marginBottom: "1.5rem" }}>
                    <h3>🤖 Your bots</h3>
                    <div className="stack">
                        {bots.map(b => (
                            <a key={b.community_id} href={`#/kibbutz/${b.community_id}`}
                                className="card" style={{ textDecoration: "none", color: "inherit", display: "block" }}>
                                <div className="row" style={{ justifyContent: "space-between" }}>
                                    <div>
                                        <div className="bold">
                                            {b.display_name || `${user.user_name}-bot`}
                                            <span className="muted" style={{ fontWeight: "normal", marginLeft: 8 }}>
                                                in {b.community_name}
                                            </span>
                                        </div>
                                        <div className="muted">
                                            {b.active ? "active" : "paused"} · {b.orientation} · init {b.initiative}/10 · agree {b.agreeableness}/10
                                            {b.last_turn_at && ` · last turn ${new Date(b.last_turn_at).toLocaleTimeString()}`}
                                        </div>
                                    </div>
                                    <span className="pill"
                                        style={{ background: b.active ? "var(--accent-soft)" : "transparent", color: b.active ? "var(--accent)" : "var(--text-dim)" }}>
                                        {b.active ? "🟢 on" : "⏸ off"}
                                    </span>
                                </div>
                            </a>
                        ))}
                    </div>
                </section>
            )}

            {pendingApps.length > 0 && (
                <section style={{ marginBottom: "1.5rem" }}>
                    <h3>Applications in flight</h3>
                    <div className="stack">
                        {pendingApps.map(a => (
                            <div key={a.proposal_id} className="card">
                                <div className="row" style={{ justifyContent: "space-between" }}>
                                    <div>
                                        <div className="bold">{a.community_name}</div>
                                        <div className="muted">
                                            {a.status} · support {a.support_count} · age {a.age}
                                        </div>
                                    </div>
                                    <WithdrawBtn proposalId={a.proposal_id} userId={user.user_id}
                                        onDone={() => setPendingApps(xs => xs.filter(x => x.proposal_id !== a.proposal_id))} />
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}

            {sentInvites.length > 0 && (
                <section>
                    <h3>Invites you've sent</h3>
                    <div className="stack">
                        {sentInvites.slice(0, 10).map(i => (
                            <div key={i.invite_id} className="card">
                                <div className="row" style={{ justifyContent: "space-between" }}>
                                    <div>
                                        <div className="bold">{i.community_name}</div>
                                        <div className="muted">
                                            {i.claimed
                                              ? `Claimed ${new Date(i.claimed_at).toLocaleDateString()}`
                                              : `Expires ${new Date(i.expires_at).toLocaleDateString()}`}
                                        </div>
                                    </div>
                                    {!i.claimed && (
                                        <button className="btn" onClick={() => {
                                            const url = window.location.origin + "/app/#/invite/" + i.invite_code;
                                            navigator.clipboard?.writeText(url);
                                        }}>📋 Copy link</button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}

function WithdrawBtn({ proposalId, userId, onDone }) {
    const [busy, setBusy] = useState(false);
    const withdraw = async () => {
        if (!confirm("Withdraw this application?")) return;
        setBusy(true);
        try {
            await api.post(`/proposals/${proposalId}/withdraw`, { user_id: userId });
            onDone?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setBusy(false); }
    };
    return <button className="btn ghost" disabled={busy} onClick={withdraw}>{busy ? "…" : "Withdraw"}</button>;
}

// ── Browse ──────────────────────────────────────────────
function BrowsePage({ user }) {
    const [q, setQ] = useState("");
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const debounce = useRef(null);

    const reload = useCallback(async (search) => {
        setLoading(true); setError(null);
        try {
            const rs = await api.get("/communities" + (search ? `?q=${encodeURIComponent(search)}` : ""));
            setRows(rs);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, []);
    useEffect(() => { reload(""); }, [reload]);

    const onSearch = (e) => {
        setQ(e.target.value);
        clearTimeout(debounce.current);
        debounce.current = setTimeout(() => reload(e.target.value), 300);
    };

    return (
        <div className="container">
            <h2>Browse kibbutzim</h2>
            <input className="input" placeholder="Search by name…" value={q} onChange={onSearch}
                   style={{ marginBottom: "1rem" }} />
            <ErrorBanner error={error} />
            {loading ? <div className="muted">Loading…</div>
             : rows.length === 0 ? (
                <Empty title="No kibbutzim found">Try a different search, or <a href="#/kibbutz/new">create one</a>.</Empty>
             ) : (
                <div className="stack">
                    {rows.map(c => (
                        <a key={c.id} href={`#/kibbutz/${c.id}`}
                           className="card" style={{ textDecoration: "none", color: "inherit", display: "block" }}>
                            <div className="row" style={{ justifyContent: "space-between" }}>
                                <div>
                                    <div className="bold">{c.name}</div>
                                    <div className="muted">{c.member_count} member{c.member_count === 1 ? "" : "s"} · created {new Date(c.created_at).toLocaleDateString()}</div>
                                </div>
                                <span className="muted">→</span>
                            </div>
                        </a>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Create Kibbutz ──────────────────────────────────────
function CreateKibbutzPage({ user }) {
    const [name, setName] = useState("");
    const [mission, setMission] = useState("");
    const [enableFinancial, setEnableFinancial] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    const submit = async (e) => {
        e.preventDefault();
        setSubmitting(true); setError(null);
        try {
            const community = await api.post("/communities", {
                name: name.trim(),
                founder_user_id: user.user_id,
                initial_artifact_mission: mission.trim() || null,
                enable_financial: enableFinancial,
            });
            navigate(`#/kibbutz/${community.id}`);
        } catch (err) { setError(err.message); }
        finally { setSubmitting(false); }
    };

    return (
        <div className="container" style={{ maxWidth: 600 }}>
            <h2>Create a kibbutz</h2>
            <p className="muted">
                You become the founder and first member. Invite others via link —
                their Membership goes to the community vote like any other proposal.
            </p>
            <form className="stack card" onSubmit={submit}>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Name</div>
                    <input className="input" required maxLength={255}
                           placeholder="e.g. Brooklyn Reading Circle"
                           value={name} onChange={(e) => setName(e.target.value)} />
                </label>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Mission (optional)</div>
                    <textarea className="input" rows={4}
                              placeholder="What will this kibbutz work on together? (This becomes the briefing for any artifact work.)"
                              value={mission} onChange={(e) => setMission(e.target.value)} />
                </label>
                <label className="row" style={{ alignItems: "flex-start", gap: "0.5rem", padding: "0.5rem", background: "rgba(78,204,163,0.08)", borderRadius: 6 }}>
                    <input type="checkbox" checked={enableFinancial}
                           onChange={(e) => setEnableFinancial(e.target.checked)}
                           style={{ marginTop: 2 }} />
                    <div>
                        <div className="bold">💰 Enable finance module</div>
                        <div className="muted" style={{ fontSize: "0.82rem" }}>
                            Adds a community wallet, funding requests from child actions,
                            payment proposals, dividends, and escrow-based membership fees.
                            You can enable this later via a ChangeVariable proposal too.
                        </div>
                    </div>
                </label>
                <div className="row" style={{ justifyContent: "flex-end" }}>
                    <a href="#/dashboard" className="btn ghost">Cancel</a>
                    <button className="btn primary" disabled={submitting || !name.trim()}>
                        {submitting ? "Creating…" : "Create kibbutz"}
                    </button>
                </div>
                <ErrorBanner error={error} />
            </form>
        </div>
    );
}

// ── Kibbutz view ────────────────────────────────────────
const PROPOSAL_STATUS_COLORS = {
    OutThere: "var(--warn)",
    OnTheAir: "var(--accent)",
    Accepted: "var(--accent)",
    Rejected: "var(--danger)",
    Canceled: "var(--text-dim)",
};

// Plain-English stage labels (jargon term still exposed on hover).
const PROPOSAL_STATUS_LABEL = {
    Draft: "Draft",
    OutThere: "Open for support",
    OnTheAir: "Active vote",
    Accepted: "Accepted",
    Rejected: "Rejected",
    Canceled: "Withdrawn",
};

// Emoji + human label per proposal type (mirrors the landing mock).
const PROPOSAL_TYPE_META = {
    Membership:          { emoji: "👋", label: "Membership" },
    ThrowOut:            { emoji: "🚪", label: "Remove member" },
    AddStatement:        { emoji: "📜", label: "Add statement" },
    RemoveStatement:     { emoji: "✂️", label: "Remove statement" },
    ReplaceStatement:    { emoji: "✏️", label: "Replace statement" },
    ChangeVariable:      { emoji: "⚙️", label: "Change setting" },
    AddAction:           { emoji: "🎯", label: "New action" },
    EndAction:           { emoji: "🛑", label: "End action" },
    JoinAction:          { emoji: "🤝", label: "Join action" },
    Funding:             { emoji: "💰", label: "Funding" },
    Payment:             { emoji: "💸", label: "Payment" },
    payBack:             { emoji: "↩️", label: "Pay back" },
    Dividend:            { emoji: "🎁", label: "Dividend" },
    SetMembershipHandler:{ emoji: "🛂", label: "Membership handler" },
    CreateArtifact:      { emoji: "📦", label: "Create artifact" },
    EditArtifact:        { emoji: "📝", label: "Edit artifact" },
    RemoveArtifact:      { emoji: "🗑️", label: "Remove artifact" },
    DelegateArtifact:    { emoji: "🔀", label: "Delegate artifact" },
    CommitArtifact:      { emoji: "🔒", label: "Commit artifact" },
};

function formatRelativeTime(iso) {
    if (!iso) return "";
    const then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 60)    return "just now";
    if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
    if (secs < 604800) return `${Math.floor(secs / 86400)}d ago`;
    return new Date(iso).toLocaleDateString();
}

function KibbutzPage({ communityId, user, onRefreshMembership }) {
    const [community, setCommunity] = useState(null);
    const [members, setMembers] = useState([]);
    const [proposals, setProposals] = useState([]);
    const [statements, setStatements] = useState([]);
    const [pulses, setPulses] = useState([]);
    const [tab, setTab] = useState("proposals");
    const [error, setError] = useState(null);
    const [inviteUrl, setInviteUrl] = useState(null);
    const [applyBusy, setApplyBusy] = useState(false);

    const imMember = useMemo(
        () => user && members.some(m => m.user_id === user.user_id),
        [user, members],
    );

    const [variables, setVariables] = useState({});
    // Member-detail modal: clicking any member name/avatar in this community
    // page opens a lightweight profile card.
    const [memberModal, setMemberModal] = useState(null);  // {user_id, seed?}

    // When ProposePage just navigated back, it stashes the new
    // proposal's id in sessionStorage so we can scroll to + highlight
    // that card. Read it once on mount; clear so the next view
    // doesn't re-animate it.
    const [newProposalId, setNewProposalId] = useState(() => {
        try {
            const id = sessionStorage.getItem("kbz-new-proposal-id");
            if (id) sessionStorage.removeItem("kbz-new-proposal-id");
            return id;
        } catch { return null; }
    });
    useEffect(() => {
        if (!newProposalId) return;
        // Let the card animate for ~3s then drop the highlight
        const t = setTimeout(() => setNewProposalId(null), 3500);
        return () => clearTimeout(t);
    }, [newProposalId]);

    const reload = useCallback(async () => {
        setError(null);
        try {
            const [c, m, p, s, v, pl] = await Promise.all([
                api.get(`/communities/${communityId}`),
                api.get(`/communities/${communityId}/members`),
                api.get(`/communities/${communityId}/proposals`),
                api.get(`/communities/${communityId}/statements`),
                api.get(`/communities/${communityId}/variables`),
                api.get(`/communities/${communityId}/pulses`),
            ]);
            setCommunity(c);
            setMembers(m);
            setProposals(p);
            setStatements(s);
            setVariables(v.variables || {});
            setPulses(pl || []);
        } catch (e) { setError(e.message); }
    }, [communityId]);
    useEffect(() => { reload(); }, [reload]);

    // Live event stream scoped to this community; auto-refreshes on
    // state-change events.
    const { events: liveEvents } = useLiveEvents({
        communityId, onRefresh: reload,
    });

    const isFinancial = (variables?.Financial || "false") !== "false"
                     && (variables?.Financial || "") !== "";
    const membershipFee = parseFloat(variables?.membershipFee || "0") || 0;

    const apply = async () => {
        if (!user) { navigate("#/login"); return; }
        if (isFinancial && membershipFee > 0) {
            const ok = confirm(
                `This kibbutz charges a ${membershipFee} credit membership fee. ` +
                `Applying will escrow ${membershipFee} credits until the community votes. ` +
                `If accepted, the fee is kept by the community. If rejected or expired, you get it back.\n\nProceed?`,
            );
            if (!ok) return;
        }
        setApplyBusy(true);
        try {
            await api.post(`/communities/${communityId}/proposals`, {
                user_id: user.user_id,
                proposal_type: "Membership",
                proposal_text: `${user.user_name} applied to join`,
                val_uuid: user.user_id,
            });
            toast("Application filed. Check your dashboard for progress.", "success");
            onRefreshMembership?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setApplyBusy(false); }
    };

    const createInvite = async () => {
        try {
            const r = await api.post(`/communities/${communityId}/invites`, {});
            setInviteUrl(window.location.origin + "/app/#/invite/" + r.code);
        } catch (e) { toast(e.message, "error"); }
    };

    if (!community) {
        return <div className="container">{error ? <ErrorBanner error={error} /> : "Loading…"}</div>;
    }

    const sortedProposals = [...proposals].sort((a, b) => {
        const order = { OutThere: 0, OnTheAir: 1, Accepted: 2, Rejected: 3, Canceled: 4, Draft: 5 };
        return (order[a.proposal_status] ?? 9) - (order[b.proposal_status] ?? 9);
    });

    return (
        <div>
            <NewsTicker events={liveEvents} communityId={communityId} />
            <div className="container">
            <PulseBar communityId={communityId} user={user}
                      imMember={imMember} pulses={pulses} members={members}
                      onChanged={reload} />
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: "0.75rem" }}>
                <div>
                    <h2 style={{ margin: 0 }}>{community.name}</h2>
                    <div className="muted">{community.member_count} members · {proposals.length} proposals</div>
                </div>
                <div className="row">
                    {imMember ? (
                        <>
                            <a href={`#/kibbutz/${communityId}/propose`} className="btn primary">+ New proposal</a>
                            <button className="btn" onClick={createInvite}>+ Invite</button>
                        </>
                    ) : user ? (
                        <button className="btn primary" disabled={applyBusy} onClick={apply}>
                            {applyBusy ? "Applying…" : "Apply to join"}
                        </button>
                    ) : (
                        <a href="#/login" className="btn primary">Sign in to join</a>
                    )}
                </div>
            </div>
            {inviteUrl && (
                <div className="card" style={{ marginTop: "1rem", background: "var(--accent-soft)" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Invite link ready</div>
                    <input className="input" readOnly value={inviteUrl}
                           onClick={(e) => e.target.select()}
                           style={{ fontFamily: "monospace", fontSize: "0.85rem" }} />
                    <div className="row" style={{ marginTop: "0.5rem" }}>
                        <button className="btn" onClick={() => navigator.clipboard?.writeText(inviteUrl)}>📋 Copy</button>
                        <button className="btn ghost" onClick={() => setInviteUrl(null)}>Close</button>
                    </div>
                </div>
            )}
            <ErrorBanner error={error} />
            <div className="row" style={{ margin: "1rem 0", borderBottom: "1px solid var(--border)" }}>
                {(() => {
                    const base = [
                        "proposals", "chat", "members",
                        "statements", "variables", "actions",
                    ];
                    if (isFinancial) base.push("treasury");
                    if (imMember) base.push("bot");
                    return base;
                })().map(t => (
                    <button key={t} className={"btn ghost" + (tab === t ? " bold" : "")}
                        onClick={() => setTab(t)}
                        style={{
                            borderRadius: 0,
                            borderBottom: tab === t ? "2px solid var(--accent)" : "2px solid transparent",
                        }}>
                        {
                            t === "bot" ? "🤖 My bot" :
                            t === "treasury" ? "💰 Treasury" :
                            t === "chat" ? "💬 Chat" :
                            t === "variables" ? "⚙️ Variables" :
                            t === "actions" ? "🌳 Actions" :
                            t[0].toUpperCase() + t.slice(1)
                        }
                    </button>
                ))}
            </div>
            {tab === "proposals" && (
                sortedProposals.length === 0 ? <Empty title="No proposals yet">Be the first to propose something.</Empty> :
                <div className="stack">
                    {sortedProposals.map(p => (
                        <ProposalCard key={p.id} proposal={p} imMember={imMember}
                            user={user} onChanged={reload}
                            onOpenMember={(uid, seed) => setMemberModal({ user_id: uid, seed })}
                            highlightNew={p.id === newProposalId} />
                    ))}
                </div>
            )}
            {tab === "members" && (
                <div className="stack">
                    {members.map(m => {
                        const label = m.display_name || m.user_name || m.user_id.slice(0, 8);
                        return (
                            <div key={m.user_id} className="card clickable"
                                 onClick={() => setMemberModal({ user_id: m.user_id, seed: m })}
                                 title={`Open ${label}'s profile`}>
                                <div className="bold">{label}</div>
                                <div className="muted">seniority {m.seniority}</div>
                            </div>
                        );
                    })}
                </div>
            )}
            {tab === "statements" && (
                statements.length === 0 ? <Empty title="No statements yet">Propose AddStatement to add a rule.</Empty> :
                <div className="stack">
                    {statements.map(s => (
                        <div key={s.id} className="card">
                            <div>{s.statement_text}</div>
                        </div>
                    ))}
                </div>
            )}
            {tab === "chat" && (
                <ChatPanel communityId={communityId} user={user}
                           imMember={imMember} members={members}
                           liveEvents={liveEvents} />
            )}
            {tab === "variables" && (
                <VariablesPanel communityId={communityId} variables={variables}
                                imMember={imMember} user={user} onChanged={reload} />
            )}
            {tab === "actions" && (
                <ActionsPanel communityId={communityId} />
            )}
            {tab === "bot" && imMember && (
                <BotConfigPanel communityId={communityId} user={user} />
            )}
            {tab === "treasury" && isFinancial && (
                <TreasuryPanel communityId={communityId} imMember={imMember} user={user} />
            )}
            </div>
            {memberModal && (
                <MemberDetailModal
                    userId={memberModal.user_id}
                    seed={memberModal.seed}
                    communityId={communityId}
                    members={members}
                    onClose={() => setMemberModal(null)}
                />
            )}
        </div>
    );
}

function ProposalCard({ proposal, imMember, user, onChanged, onOpenMember, highlightNew }) {
    const [supporting, setSupporting] = useState(false);
    const [detailOpen, setDetailOpen] = useState(false);
    const cardRef = useRef(null);

    useEffect(() => {
        if (highlightNew && cardRef.current) {
            cardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }, [highlightNew]);

    const canAct = imMember && (proposal.proposal_status === "OutThere" || proposal.proposal_status === "OnTheAir");
    const typeMeta = PROPOSAL_TYPE_META[proposal.proposal_type] || { emoji: "📋", label: proposal.proposal_type };
    const statusLabel = PROPOSAL_STATUS_LABEL[proposal.proposal_status] || proposal.proposal_status;
    const statusClass = "pc-status " + (proposal.proposal_status || "").toLowerCase();

    // Choose which threshold line to draw: OutThere = promote_threshold
    // (move to active vote); OnTheAir / decided states = decide_threshold
    // (execution line). Fall back gracefully when enrichment is missing.
    const isDecisionPhase = ["OnTheAir","Accepted","Rejected"].includes(proposal.proposal_status);
    const threshold = isDecisionPhase
        ? (proposal.decide_threshold ?? proposal.promote_threshold ?? null)
        : (proposal.promote_threshold ?? null);
    const supportCount = proposal.support_count ?? 0;
    const hasThreshold = threshold != null && threshold > 0;
    const denom = hasThreshold ? Math.max(supportCount, threshold) : Math.max(supportCount, 1);
    const fillPct = Math.min(100, Math.round((supportCount / denom) * 100));
    const tickPct = hasThreshold ? Math.min(100, Math.round((threshold / denom) * 100)) : null;

    const authorName = proposal.display_name || proposal.user_name || "Member";
    const initial = (authorName || "?").trim().charAt(0).toUpperCase();
    // Prefer pitch as the card body (proposer's "why"). Fall back to
    // proposal_text if it differs from the title, so legacy rows still
    // show something useful until we backfill pitches.
    const pitch = (proposal.pitch || "").trim();
    const textBody = proposal.proposal_text && proposal.val_text && proposal.proposal_text !== proposal.val_text
        ? proposal.proposal_text : null;
    const body = pitch || textBody;
    const title = proposal.val_text || proposal.proposal_text || "(untitled)";

    const support = async (e) => {
        e?.stopPropagation?.();
        setSupporting(true);
        try {
            await api.post(`/proposals/${proposal.id}/support`, { user_id: user.user_id });
            onChanged?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setSupporting(false); }
    };

    return (
        <>
            <div className={"card proposal-card" + (highlightNew ? " proposal-new-pulse" : "")}
                 ref={cardRef}
                 onClick={() => setDetailOpen(true)}
                 title="Click for details, supporters, and comments">
                <div className="pc-head">
                    <span className="pc-type">
                        <span>{typeMeta.emoji}</span> {typeMeta.label}
                    </span>
                    <span className={statusClass} title={proposal.proposal_status}>
                        {statusLabel}
                    </span>
                </div>

                <div className="pc-author">
                    <span className="pc-avatar clickable"
                          onClick={(e) => {
                              e.stopPropagation();
                              if (proposal.user_id) onOpenMember?.(proposal.user_id, {
                                  user_id: proposal.user_id,
                                  user_name: proposal.user_name,
                                  display_name: proposal.display_name,
                              });
                          }}
                          title={`Open ${authorName}'s profile`}>{initial}</span>
                    <span className="pc-author-name clickable"
                          onClick={(e) => {
                              e.stopPropagation();
                              if (proposal.user_id) onOpenMember?.(proposal.user_id, {
                                  user_id: proposal.user_id,
                                  user_name: proposal.user_name,
                                  display_name: proposal.display_name,
                              });
                          }}>{authorName}</span>
                    <span className="pc-author-meta">
                        &middot; {formatRelativeTime(proposal.created_at) || `age ${proposal.age}`}
                    </span>
                </div>

                <div className="pc-title">{title}</div>
                {body && <div className="pc-body">{body}</div>}

                <div className="pc-support">
                    <div className="pc-support-labels">
                        <span><strong>{supportCount}</strong> supporter{supportCount === 1 ? "" : "s"}</span>
                        {hasThreshold && (
                            <span>threshold: {threshold} {isDecisionPhase ? "(to pass)" : "(to open vote)"}</span>
                        )}
                    </div>
                    <div className="pc-support-track">
                        <div className="pc-support-fill" style={{ width: `${fillPct}%` }}></div>
                        {tickPct != null && (
                            <div className="pc-threshold-tick" style={{ left: `${tickPct}%` }}></div>
                        )}
                    </div>
                </div>

                <div className="pc-footer">
                    <span className="pc-meta">age {proposal.age}{proposal.pulse_id ? " · voting now" : ""}</span>
                    {canAct && (
                        <button className="btn primary pc-support-btn"
                                disabled={supporting} onClick={support}>
                            {supporting ? "…" : "👍 Support"}
                        </button>
                    )}
                </div>
            </div>
            {detailOpen && (
                <ProposalDetailModal proposal={proposal} user={user} imMember={imMember}
                    onClose={() => setDetailOpen(false)} onChanged={onChanged} />
            )}
        </>
    );
}

// ── Comment thread — nested replies + up/down votes ─────
// The agent can reply_comment and vote_comment; humans get the same
// surface here. Replies render one level deep (indented); deeper
// replies render as flat siblings under their closest ancestor to
// keep the modal readable.
function CommentThread({ comments, user, canReply, onReload }) {
    const byParent = useMemo(() => {
        const m = new Map();
        for (const c of comments) {
            const key = c.parent_comment_id || null;
            if (!m.has(key)) m.set(key, []);
            m.get(key).push(c);
        }
        return m;
    }, [comments]);

    const roots = byParent.get(null) || [];
    return (
        <div className="stack" style={{ gap: "0.5rem" }}>
            {roots.map(c => (
                <CommentNode key={c.id} c={c} byParent={byParent}
                    user={user} canReply={canReply} onReload={onReload}
                    depth={0} />
            ))}
        </div>
    );
}

function CommentNode({ c, byParent, user, canReply, onReload, depth }) {
    const [replying, setReplying] = useState(false);
    const [replyDraft, setReplyDraft] = useState("");
    const [voteBusy, setVoteBusy] = useState(false);
    const [replyBusy, setReplyBusy] = useState(false);
    const children = byParent.get(c.id) || [];

    const vote = async (delta) => {
        if (!user || voteBusy) return;
        setVoteBusy(true);
        try {
            await api.post(`/comments/${c.id}/score`, { delta });
            await onReload?.();
        } catch (e) { toast(e.message, "error"); }
        finally { setVoteBusy(false); }
    };

    const submitReply = async (e) => {
        e?.preventDefault?.();
        const text = replyDraft.trim();
        if (!text || !user) return;
        setReplyBusy(true);
        try {
            await api.post(
                `/entities/${c.entity_type}/${c.entity_id}/comments`,
                {
                    user_id: user.user_id,
                    comment_text: text,
                    parent_comment_id: c.id,
                },
            );
            setReplyDraft("");
            setReplying(false);
            await onReload?.();
        } catch (err) { toast(err.message, "error"); }
        finally { setReplyBusy(false); }
    };

    return (
        <div style={{ marginLeft: depth > 0 ? 14 : 0 }}>
            <div style={{
                padding: "0.5rem 0.7rem",
                background: "rgba(0,0,0,0.04)",
                borderRadius: 6,
                borderLeft: depth > 0 ? "2px solid var(--border)" : "none",
            }}>
                <div className="muted" style={{ fontSize: "0.72rem" }}>
                    {(c.user_name || c.user_id || "").slice(0, 16)}
                    {" · "}
                    {new Date(c.created_at).toLocaleTimeString()}
                </div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: "0.9rem" }}>
                    {c.comment_text}
                </div>
                <div className="row" style={{
                    gap: "0.6rem", marginTop: 4, fontSize: "0.75rem",
                }}>
                    {user && (
                        <>
                            <button className="btn ghost" disabled={voteBusy}
                                    onClick={() => vote(1)}
                                    style={{ padding: "0 6px", fontSize: "0.8rem" }}
                                    title="Upvote">▲</button>
                            <span className="muted" style={{ minWidth: 16, textAlign: "center" }}>
                                {c.score || 0}
                            </span>
                            <button className="btn ghost" disabled={voteBusy}
                                    onClick={() => vote(-1)}
                                    style={{ padding: "0 6px", fontSize: "0.8rem" }}
                                    title="Downvote">▼</button>
                        </>
                    )}
                    {canReply && !replying && depth < 3 && (
                        <button className="btn ghost"
                                onClick={() => setReplying(true)}
                                style={{ padding: "0 6px", fontSize: "0.75rem" }}>
                            ↩ Reply
                        </button>
                    )}
                </div>
                {replying && (
                    <form onSubmit={submitReply} style={{ marginTop: 6 }}>
                        <textarea className="input" rows={2} maxLength={300}
                                  autoFocus
                                  placeholder="Reply…" value={replyDraft}
                                  onChange={(e) => setReplyDraft(e.target.value)} />
                        <div className="row" style={{ justifyContent: "flex-end", gap: "0.4rem", marginTop: 4 }}>
                            <button type="button" className="btn ghost"
                                    onClick={() => { setReplying(false); setReplyDraft(""); }}>
                                Cancel
                            </button>
                            <button className="btn primary"
                                    disabled={replyBusy || !replyDraft.trim()}>
                                {replyBusy ? "…" : "Reply"}
                            </button>
                        </div>
                    </form>
                )}
            </div>
            {children.length > 0 && (
                <div className="stack" style={{ gap: "0.35rem", marginTop: "0.35rem" }}>
                    {children.map(ch => (
                        <CommentNode key={ch.id} c={ch} byParent={byParent}
                            user={user} canReply={canReply} onReload={onReload}
                            depth={depth + 1} />
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Proposal detail modal — supporters + comments + inline actions ─
function ProposalDetailModal({ proposal, user, imMember, onClose, onChanged }) {
    const [supporters, setSupporters] = useState([]);
    const [comments, setComments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [draft, setDraft] = useState("");
    const [busy, setBusy] = useState(null);  // "support" | "comment" | "withdraw"
    const [error, setError] = useState(null);

    const reload = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const [sup, cmts] = await Promise.all([
                api.get(`/proposals/${proposal.id}/supporters`).catch(() => []),
                api.get(`/entities/proposal/${proposal.id}/comments?limit=100`)
                    .catch(() => []),
            ]);
            setSupporters(sup || []);
            setComments(cmts || []);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, [proposal.id]);
    useEffect(() => { reload(); }, [reload]);

    const color = PROPOSAL_STATUS_COLORS[proposal.proposal_status] || "var(--text-dim)";
    const canSupport = imMember && (proposal.proposal_status === "OutThere" || proposal.proposal_status === "OnTheAir");
    const canComment = imMember && canSupport;
    const isAuthor = user && proposal.user_id === user.user_id;
    const canWithdraw = isAuthor && (proposal.proposal_status === "OutThere" || proposal.proposal_status === "Draft");
    const alreadySupported = user && supporters.some(s => (s.user_id || s) === user.user_id);

    const doSupport = async () => {
        if (!user) return;
        setBusy("support"); setError(null);
        try {
            await api.post(`/proposals/${proposal.id}/support`,
                { user_id: user.user_id });
            await reload();
            onChanged?.();
        } catch (e) { setError(e.message); }
        finally { setBusy(null); }
    };

    const doComment = async (e) => {
        e?.preventDefault?.();
        if (!user) return;
        const text = draft.trim();
        if (!text) return;
        setBusy("comment"); setError(null);
        try {
            await api.post(`/entities/proposal/${proposal.id}/comments`, {
                user_id: user.user_id, comment_text: text,
            });
            setDraft("");
            await reload();
            onChanged?.();
        } catch (e) { setError(e.message); }
        finally { setBusy(null); }
    };

    const doWithdraw = async () => {
        if (!confirm("Withdraw this proposal? Status becomes Canceled.")) return;
        setBusy("withdraw"); setError(null);
        try {
            await api.post(`/proposals/${proposal.id}/withdraw`,
                { user_id: user.user_id });
            onChanged?.();
            onClose();
        } catch (e) { setError(e.message); }
        finally { setBusy(null); }
    };

    return (
        <div style={{
            position: "fixed", inset: 0, zIndex: 60,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: "1rem",
        }} onClick={onClose}>
            <div className="card" style={{
                maxWidth: 640, width: "100%", maxHeight: "92vh",
                overflow: "auto", padding: "1.4rem",
            }} onClick={(e) => e.stopPropagation()}>
                <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.6rem" }}>
                    <div className="row">
                        <span className="pill" style={{ background: `${color}22`, color }}>{proposal.proposal_type}</span>
                        <span className="pill" style={{ background: "transparent", border: `1px solid ${color}`, color }}>
                            {proposal.proposal_status}
                        </span>
                    </div>
                    <button className="btn ghost" onClick={onClose}
                            style={{ fontSize: "1.2rem", padding: "0 0.5rem" }}>×</button>
                </div>
                <h3 style={{ marginTop: 0 }}>
                    {proposal.val_text || proposal.proposal_text || "(untitled)"}
                </h3>
                {proposal.val_text && proposal.proposal_text
                    && proposal.val_text !== proposal.proposal_text && (
                    <div className="muted" style={{
                        whiteSpace: "pre-wrap", marginBottom: "0.6rem",
                    }}>{proposal.proposal_text}</div>
                )}
                {proposal.pitch && proposal.pitch.trim() && (
                    <div className="pitch-block">
                        <div className="pitch-label">Pitch — why this should pass</div>
                        <div className="pitch-text">{proposal.pitch}</div>
                    </div>
                )}
                <div className="muted" style={{ fontSize: "0.82rem" }}>
                    age {proposal.age} · support {proposal.support_count} · created{" "}
                    {new Date(proposal.created_at).toLocaleString()}
                </div>

                <div className="row" style={{ margin: "1rem 0", flexWrap: "wrap", gap: "0.5rem" }}>
                    {canSupport && (
                        <button className="btn primary" onClick={doSupport}
                                disabled={busy === "support" || alreadySupported}>
                            {alreadySupported ? "✓ Supported" :
                             busy === "support" ? "…" : "👍 Support"}
                        </button>
                    )}
                    {canWithdraw && (
                        <button className="btn ghost" onClick={doWithdraw}
                                disabled={busy === "withdraw"}>
                            {busy === "withdraw" ? "…" : "↩ Withdraw"}
                        </button>
                    )}
                </div>

                <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.8rem" }}>
                    <div className="bold" style={{ marginBottom: "0.4rem" }}>
                        👥 Supporters ({supporters.length})
                    </div>
                    {loading ? (
                        <div className="muted">Loading…</div>
                    ) : supporters.length === 0 ? (
                        <div className="muted" style={{ fontSize: "0.85rem" }}>No supporters yet.</div>
                    ) : (
                        <div className="row" style={{ gap: "0.3rem", flexWrap: "wrap" }}>
                            {supporters.map((s, i) => (
                                <span key={i} className="pill" style={{
                                    background: "var(--accent-soft)", color: "var(--accent)",
                                }}>{(s.display_name || s.user_name || s.user_id || "").slice(0, 16)}</span>
                            ))}
                        </div>
                    )}
                </div>

                <div style={{ borderTop: "1px solid var(--border)", marginTop: "0.8rem", paddingTop: "0.8rem" }}>
                    <div className="bold" style={{ marginBottom: "0.4rem" }}>
                        💬 Comments ({comments.length})
                    </div>
                    {loading ? <div className="muted">Loading…</div>
                     : comments.length === 0 ? (
                        <div className="muted" style={{ fontSize: "0.85rem" }}>
                            No comments yet.{canComment && " Leave the first."}
                        </div>
                    ) : (
                        <CommentThread comments={comments} user={user}
                            canReply={canComment} onReload={reload} />
                    )}
                    {canComment && (
                        <form className="stack" onSubmit={doComment} style={{ marginTop: "0.8rem" }}>
                            <textarea className="input" rows={2} maxLength={300}
                                      placeholder="Add a comment (300 char max, be punchy)…"
                                      value={draft}
                                      onChange={(e) => setDraft(e.target.value)} />
                            <div className="row" style={{ justifyContent: "space-between" }}>
                                <span className="muted" style={{ fontSize: "0.75rem" }}>
                                    {draft.length}/300
                                </span>
                                <button className="btn primary"
                                        disabled={busy === "comment" || !draft.trim()}>
                                    {busy === "comment" ? "…" : "Post comment"}
                                </button>
                            </div>
                        </form>
                    )}
                </div>
                <ErrorBanner error={error} />
            </div>
        </div>
    );
}

// ── Propose form ────────────────────────────────────────
// Full proposal-type catalog with per-type field specs.
// `needs` declares which of {text, val_text, val_uuid} are required;
// `pickFrom` says which existing-entity list the val_uuid dropdown
// should be populated from (statements | members | actions | artifacts
// | containers). `financialOnly` hides the type unless the community
// has the Financial module on.
const PROPOSAL_CATALOG = [
    // ── Governance
    { value: "AddStatement",    group: "Governance",  label: "Add a statement (rule)",
      help: "Describe the rule. If accepted, it becomes a community statement.",
      needs: { text: true } },
    { value: "RemoveStatement", group: "Governance",  label: "Remove a statement",
      help: "Pick which existing statement to retire, and say why.",
      needs: { text: true, val_uuid: true },
      pickFrom: "statements", val_uuid_label: "Statement to remove" },
    { value: "ReplaceStatement", group: "Governance", label: "Replace an existing statement",
      help: "Pick the statement to replace. Your description is the new text.",
      needs: { text: true, val_uuid: true },
      pickFrom: "statements", val_uuid_label: "Statement to replace" },
    { value: "ChangeVariable",  group: "Governance",  label: "Change a governance variable",
      help: "Variable name in Description, new value below. See the ⚙️ Variables tab for names.",
      needs: { text: true, val_text: true },
      text_placeholder: "e.g. PulseSupport", val_text_label: "New value", val_text_placeholder: "e.g. 60" },
    { value: "ThrowOut",        group: "Governance",  label: "Throw out a member",
      help: "Pick who and say why. Needs community support to pass.",
      needs: { text: true, val_uuid: true },
      pickFrom: "members", val_uuid_label: "Member to remove" },

    // ── Actions (working groups)
    { value: "AddAction",       group: "Actions",     label: "Start a new action (working group)",
      help: "Describe the work. Short name becomes the group's label.",
      needs: { text: true, val_text: true },
      val_text_label: "Short name", val_text_placeholder: "e.g. Onboarding Writers" },
    { value: "EndAction",       group: "Actions",     label: "Close an action",
      help: "Pick a sub-action to close. Its wallet (if any) sweeps back to parent.",
      needs: { text: true, val_uuid: true },
      pickFrom: "actions", val_uuid_label: "Action to close" },
    { value: "JoinAction",      group: "Actions",     label: "Join an action",
      help: "Pick an action to join. You become a member of its sub-community.",
      needs: { text: true, val_uuid: true },
      pickFrom: "actions", val_uuid_label: "Action to join" },

    // ── Artifacts (collaborative documents)
    { value: "CreateArtifact",  group: "Artifacts",   label: "Add a section (empty slot)",
      help: "Creates an EMPTY artifact with a title in the chosen container. Filling comes via EditArtifact.",
      needs: { val_text: true, val_uuid: true },
      pickFrom: "containers", val_uuid_label: "Container",
      val_text_label: "Section title", val_text_placeholder: "e.g. Conflict Resolution Steps" },
    { value: "EditArtifact",    group: "Artifacts",   label: "Edit an artifact",
      help: "Description is the NEW body. Current content is replaced verbatim on accept.",
      needs: { text: true, val_uuid: true },
      pickFrom: "artifacts", val_uuid_label: "Artifact to edit",
      text_placeholder: "Full new content…", text_rows: 8 },
    { value: "DelegateArtifact", group: "Artifacts",  label: "Delegate an artifact to an action",
      help: "Hand an artifact to a sub-action to fill in. val_text = target action community id.",
      needs: { text: true, val_uuid: true, val_text: true },
      pickFrom: "artifacts", val_uuid_label: "Artifact",
      val_text_label: "Target action community id" },
    { value: "RemoveArtifact",  group: "Artifacts",   label: "Remove an artifact",
      help: "Retire an artifact. Usually for placeholders or duplicates.",
      needs: { text: true, val_uuid: true },
      pickFrom: "artifacts", val_uuid_label: "Artifact to remove" },
    { value: "CommitArtifact",  group: "Artifacts",   label: "Commit a container",
      help: "Lock in all ACTIVE artifacts in the container as its final output.",
      needs: { text: true, val_uuid: true },
      pickFrom: "containers", val_uuid_label: "Container to commit" },

    // ── Finance (only visible on financial kibbutzim)
    { value: "Funding",         group: "Finance",     label: "Funding (parent → child action)",
      help: "Transfer credits from this community to a child action.",
      needs: { text: true, val_uuid: true, val_text: true },
      financialOnly: true, pickFrom: "actions", val_uuid_label: "Target action",
      val_text_label: "Amount (credits)", val_text_placeholder: "e.g. 50" },
    { value: "Payment",         group: "Finance",     label: "Payment (out of community)",
      help: "Burn credits from this community (leaf only — no active sub-actions). Phase 1 is log-only.",
      needs: { text: true, val_text: true },
      financialOnly: true, val_text_label: "Amount", val_text_placeholder: "e.g. 20",
      text_placeholder: "Who's being paid and why?" },
    { value: "payBack",         group: "Finance",     label: "PayBack (external inbound refund)",
      help: "Mint credits back into the community wallet (e.g., a refund or return).",
      needs: { text: true, val_text: true },
      financialOnly: true, val_text_label: "Amount", val_text_placeholder: "e.g. 15" },
    { value: "Dividend",        group: "Finance",     label: "Dividend (split to members)",
      help: "Split an amount equally across active members' user wallets.",
      needs: { text: true, val_text: true },
      financialOnly: true, val_text_label: "Amount", val_text_placeholder: "e.g. 100" },

    // ── Advanced / rare
    { value: "SetMembershipHandler", group: "Advanced", label: "Set membership-handler action",
      help: "Delegate admission decisions to a specific action. Usually not needed.",
      needs: { text: true, val_uuid: true },
      pickFrom: "actions", val_uuid_label: "Handler action" },
];

// Groups in display order
const PROPOSAL_GROUPS = ["Governance", "Actions", "Artifacts", "Finance", "Advanced"];

// ── Bot delegation (per-kibbutz) ────────────────────────
const BOT_ORIENTATIONS = [
    { value: "pragmatist",      label: "Pragmatist — balanced, cares about getting things done" },
    { value: "producer",        label: "Producer — proposes concrete work, fills artifacts" },
    { value: "consensus",       label: "Consensus-builder — tries to find common ground" },
    { value: "devils_advocate", label: "Devil's advocate — pushes back, asks hard questions" },
    { value: "idealist",        label: "Idealist — values-first, champions principles" },
    { value: "diplomat",        label: "Diplomat — keeps conversation warm and productive" },
];

function Slider({ label, value, onChange, hint }) {
    return (
        <label style={{ display: "block", marginBottom: "0.75rem" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="bold">{label}</span>
                <span className="muted">{value} / 10</span>
            </div>
            <input type="range" min={1} max={10} value={value}
                   onChange={(e) => onChange(parseInt(e.target.value, 10))}
                   style={{ width: "100%" }} />
            {hint && <div className="muted" style={{ fontSize: "0.8rem" }}>{hint}</div>}
        </label>
    );
}

// ── Treasury panel ──────────────────────────────────────
// ── Chat tab — community-level comments as live chat ────
function ChatPanel({ communityId, user, imMember, members, liveEvents }) {
    const [messages, setMessages] = useState([]);
    const [loading, setLoading] = useState(true);
    const [draft, setDraft] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState(null);
    const bottomRef = useRef(null);

    const load = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const msgs = await api.get(
                `/entities/community/${communityId}/comments?limit=50`,
            );
            // API returns newest-first; reverse for natural chat order
            setMessages([...msgs].reverse());
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, [communityId]);

    useEffect(() => { load(); }, [load]);

    // Refresh on any comment.posted event for this community
    useEffect(() => {
        if (!liveEvents || liveEvents.length === 0) return;
        const latest = liveEvents[0];
        if (latest.event_type === "comment.posted") load();
    }, [liveEvents, load]);

    // Auto-scroll to bottom when new message arrives
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, [messages]);

    const send = async (e) => {
        e?.preventDefault?.();
        const text = draft.trim();
        if (!text || !user) return;
        setSending(true); setError(null);
        try {
            await api.post(`/entities/community/${communityId}/comments`, {
                user_id: user.user_id,
                comment_text: text,
            });
            setDraft("");
            await load();
        } catch (err) { setError(err.message); }
        finally { setSending(false); }
    };

    const nameFor = (uid) => {
        const m = members.find(m => m.user_id === uid);
        return m?.user_name || uid.slice(0, 8);
    };

    return (
        <div className="stack">
            <div className="card" style={{
                maxHeight: 500, overflow: "auto",
                display: "flex", flexDirection: "column", gap: "0.5rem",
            }}>
                {loading && <div className="muted">Loading chat…</div>}
                {!loading && messages.length === 0 && (
                    <div className="muted" style={{ textAlign: "center", padding: "1rem" }}>
                        No messages yet. Say hi.
                    </div>
                )}
                {messages.map(m => {
                    const mine = m.user_id === user?.user_id;
                    return (
                        <div key={m.id} style={{
                            alignSelf: mine ? "flex-end" : "flex-start",
                            maxWidth: "80%",
                            background: mine ? "var(--accent-soft)" : "rgba(0,0,0,0.04)",
                            borderRadius: 10,
                            padding: "0.5rem 0.8rem",
                        }}>
                            <div className="muted" style={{ fontSize: "0.72rem", marginBottom: 2 }}>
                                {nameFor(m.user_id)} · {new Date(m.created_at).toLocaleTimeString()}
                            </div>
                            <div style={{ whiteSpace: "pre-wrap", fontSize: "0.9rem" }}>
                                {m.comment_text}
                            </div>
                        </div>
                    );
                })}
                <div ref={bottomRef} />
            </div>
            {imMember ? (
                <form className="row" onSubmit={send} style={{ gap: "0.5rem" }}>
                    <input className="input" placeholder="Message the community…"
                           value={draft}
                           onChange={(e) => setDraft(e.target.value)}
                           maxLength={300}
                           style={{ flex: 1 }} />
                    <button className="btn primary" disabled={sending || !draft.trim()}>
                        {sending ? "…" : "Send"}
                    </button>
                </form>
            ) : (
                <div className="muted" style={{ textAlign: "center", padding: "0.5rem" }}>
                    Only members can post. Apply to join above.
                </div>
            )}
            <ErrorBanner error={error} />
        </div>
    );
}

// ── Member detail modal — profile card + their proposals in this community
function MemberDetailModal({ userId, seed, communityId, members, onClose }) {
    const seedRow = seed || members.find(m => m.user_id === userId) || null;
    const [user, setUser] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        Promise.all([
            api.get(`/users/${userId}`).catch(() => null),
            api.get(`/communities/${communityId}/proposals?user_id=${userId}`).catch(() => []),
        ]).then(([u, props]) => {
            setUser(u);
            setProposals(Array.isArray(props) ? props : []);
        }).finally(() => setLoading(false));
    }, [userId, communityId]);

    const name = seedRow?.display_name || seedRow?.user_name
        || user?.user_name || userId.slice(0, 8);
    const initial = (name || "?").trim().charAt(0).toUpperCase();
    const seniority = seedRow?.seniority;
    const joinedAt = seedRow?.joined_at
        ? new Date(seedRow.joined_at).toLocaleDateString()
        : null;
    const isBot = seedRow?.display_name && seedRow?.user_name
        && seedRow.display_name !== seedRow.user_name;

    return (
        <div style={{
            position: "fixed", inset: 0, zIndex: 60,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: "1rem",
        }} onClick={onClose}>
            <div className="card" style={{
                maxWidth: 560, width: "100%", maxHeight: "92vh",
                overflow: "auto", padding: "1.4rem",
            }} onClick={(e) => e.stopPropagation()}>
                <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.8rem" }}>
                    <div className="row" style={{ gap: "0.7rem" }}>
                        <span className="pc-avatar" style={{ width: 44, height: 44, fontSize: "1.2rem" }}>
                            {initial}
                        </span>
                        <div>
                            <div className="bold" style={{ fontSize: "1.1rem" }}>{name}</div>
                            {isBot && <span className="pill">bot</span>}
                            {seedRow?.user_name && seedRow?.display_name
                                && seedRow.display_name !== seedRow.user_name && (
                                <span className="muted" style={{ fontSize: "0.82rem", marginLeft: 6 }}>
                                    @{seedRow.user_name}
                                </span>
                            )}
                        </div>
                    </div>
                    <button className="btn ghost" onClick={onClose}
                            style={{ fontSize: "1.2rem", padding: "0 0.5rem" }}>×</button>
                </div>

                <div className="row" style={{ gap: "1.2rem", flexWrap: "wrap", marginBottom: "0.8rem" }}>
                    {seniority != null && (
                        <div>
                            <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase" }}>
                                Seniority
                            </div>
                            <div className="bold">{seniority}</div>
                        </div>
                    )}
                    {joinedAt && (
                        <div>
                            <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase" }}>
                                Joined
                            </div>
                            <div className="bold">{joinedAt}</div>
                        </div>
                    )}
                    <div>
                        <div className="muted" style={{ fontSize: "0.72rem", textTransform: "uppercase" }}>
                            Proposals filed
                        </div>
                        <div className="bold">{proposals.length}</div>
                    </div>
                </div>

                {user?.about && (
                    <div style={{
                        padding: "0.6rem 0.8rem",
                        background: "var(--accent-soft)", borderRadius: 8,
                        marginBottom: "0.8rem", fontSize: "0.9rem",
                    }}>
                        {user.about}
                    </div>
                )}

                <div style={{ borderTop: "1px solid var(--border)", paddingTop: "0.8rem" }}>
                    <div className="bold" style={{ marginBottom: "0.4rem" }}>
                        📝 Proposals in this kibbutz ({proposals.length})
                    </div>
                    {loading ? (
                        <div className="muted">Loading…</div>
                    ) : proposals.length === 0 ? (
                        <div className="muted" style={{ fontSize: "0.85rem" }}>
                            Hasn't proposed anything here yet.
                        </div>
                    ) : (
                        <div className="stack" style={{ gap: "0.35rem" }}>
                            {proposals.slice(0, 20).map(p => {
                                const title = p.val_text || p.proposal_text || "(untitled)";
                                return (
                                    <div key={p.id} style={{
                                        padding: "0.4rem 0",
                                        borderBottom: "1px solid var(--border)",
                                        fontSize: "0.88rem",
                                    }}>
                                        <div className="row" style={{ justifyContent: "space-between", gap: "0.5rem" }}>
                                            <div style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
                                                <span className="muted" style={{ fontSize: "0.75rem", marginRight: 6 }}>
                                                    {p.proposal_type}
                                                </span>
                                                {title.slice(0, 80)}{title.length > 80 ? "…" : ""}
                                            </div>
                                            <span className="muted" style={{ fontSize: "0.75rem" }}>
                                                {p.proposal_status}
                                            </span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

// ── Variables tab — view + propose a ChangeVariable ────
function VariablesPanel({ communityId, variables, imMember, user, onChanged }) {
    const [editing, setEditing] = useState(null);  // variable name being edited
    const [draftValue, setDraftValue] = useState("");
    const [draftPitch, setDraftPitch] = useState("");

    const startEdit = (name) => {
        setEditing(name);
        setDraftValue(variables[name] || "");
        setDraftPitch("");
    };

    const propose = async (name) => {
        if (!user) return;
        const newValue = draftValue.trim();
        if (!newValue) { toast("Value can't be empty", "error"); return; }
        try {
            const body = {
                user_id: user.user_id,
                proposal_type: "ChangeVariable",
                proposal_text: name,
                val_text: newValue,
            };
            if (draftPitch.trim()) body.pitch = draftPitch.trim();
            const resp = await api.post(`/communities/${communityId}/proposals`, body);
            try { await api._fetch(`/proposals/${resp.id}/submit`, { method: "PATCH" }); } catch {}
            toast(`ChangeVariable proposal filed: ${name} → ${newValue}`, "success");
            setEditing(null);
            setDraftValue("");
            setDraftPitch("");
            onChanged?.();
        } catch (e) { toast(e.message, "error"); }
    };

    // Group for readability
    const groups = {
        "Governance thresholds (% of members)": [
            "PulseSupport", "ProposalSupport", "Membership", "ThrowOut",
            "AddStatement", "RemoveStatement", "ReplaceStatement",
            "AddAction", "EndAction", "JoinAction", "ChangeVariable",
            "Funding", "Payment", "payBack", "Dividend",
        ],
        "Community settings": [
            "Name", "MaxAge", "MinCommittee", "seniorityWeight",
            "membershipFee", "dividendBySeniority", "proposalCooldown",
            "quorumThreshold", "Financial", "membershipHandler",
        ],
        "Artifact governance (% of members)": [
            "CreateArtifact", "EditArtifact", "RemoveArtifact",
            "DelegateArtifact", "CommitArtifact",
        ],
    };
    // Bucket unknown vars into "Other"
    const known = new Set([].concat(...Object.values(groups)));
    const other = Object.keys(variables).filter(k => !known.has(k));
    if (other.length) groups["Other"] = other;

    return (
        <div className="stack">
            {Object.entries(groups).map(([label, names]) => {
                const rows = names.filter(n => n in variables);
                if (rows.length === 0) return null;
                return (
                    <div key={label} className="card">
                        <div className="bold" style={{ marginBottom: "0.5rem" }}>{label}</div>
                        <div className="stack" style={{ gap: "0.3rem" }}>
                            {rows.map(n => (
                                <div key={n} style={{
                                    padding: "0.45rem 0",
                                    borderBottom: "1px solid var(--border)",
                                }}>
                                    <div className="row" style={{ justifyContent: "space-between" }}>
                                        <div>
                                            <div className="bold" style={{ fontSize: "0.9rem" }}>{n}</div>
                                            {editing !== n && (
                                                <code style={{
                                                    background: "rgba(0,0,0,0.04)",
                                                    padding: "1px 6px", borderRadius: 3,
                                                    fontSize: "0.82rem",
                                                }}>{variables[n] || "(empty)"}</code>
                                            )}
                                        </div>
                                        {imMember && editing !== n && (
                                            <button className="btn ghost" style={{ fontSize: "0.8rem" }}
                                                    onClick={() => startEdit(n)}>
                                                Propose change
                                            </button>
                                        )}
                                    </div>
                                    {editing === n && (
                                        <div className="stack" style={{ gap: "0.4rem", marginTop: 6 }}>
                                            <label>
                                                <div className="muted" style={{ fontSize: "0.78rem", marginBottom: 2 }}>
                                                    New value
                                                </div>
                                                <input className="input" autoFocus
                                                       value={draftValue}
                                                       onChange={(e) => setDraftValue(e.target.value)}
                                                       onKeyDown={(e) => {
                                                           if (e.key === "Escape") setEditing(null);
                                                       }}
                                                       style={{ maxWidth: 260 }} />
                                            </label>
                                            <label>
                                                <div className="muted" style={{ fontSize: "0.78rem", marginBottom: 2 }}>
                                                    Pitch — why this change? <span style={{ opacity: 0.7 }}>(optional)</span>
                                                </div>
                                                <textarea className="input" rows={2}
                                                          placeholder="A short case. 1–2 sentences."
                                                          value={draftPitch}
                                                          onChange={(e) => setDraftPitch(e.target.value)} />
                                            </label>
                                            <div className="row" style={{ gap: "0.4rem" }}>
                                                <button className="btn primary" style={{ fontSize: "0.8rem" }}
                                                        onClick={() => propose(n)}>File proposal</button>
                                                <button className="btn ghost" style={{ fontSize: "0.8rem" }}
                                                        onClick={() => setEditing(null)}>Cancel</button>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                );
            })}
            {imMember && (
                <div className="muted" style={{ fontSize: "0.8rem", padding: "0 0.5rem" }}>
                    Editing a variable files a <code>ChangeVariable</code> proposal —
                    the community votes like any other decision. Enter-to-submit,
                    Esc to cancel.
                </div>
            )}
        </div>
    );
}

// ── Action tree tab — navigate child action-communities ─
function ActionsPanel({ communityId }) {
    const [actions, setActions] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        setLoading(true); setError(null);
        api.get(`/communities/${communityId}/children`)
            .then(setActions)
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, [communityId]);

    if (loading) return <div className="muted">Loading actions…</div>;
    if (error) return <ErrorBanner error={error} />;
    if (actions.length === 0) {
        return (
            <Empty title="No sub-actions yet">
                Actions are focused working groups. File an <code>AddAction</code>{" "}
                proposal via <strong>+ New proposal</strong> above to create one.
            </Empty>
        );
    }

    return (
        <div className="stack">
            {actions.map(a => (
                <a key={a.id} href={`#/kibbutz/${a.id}`}
                   className="card" style={{
                       textDecoration: "none", color: "inherit", display: "block",
                   }}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                        <div>
                            <div className="bold">🌳 {a.name}</div>
                            <div className="muted" style={{ fontSize: "0.82rem" }}>
                                {a.member_count} member{a.member_count === 1 ? "" : "s"}
                                {a.status !== 1 && " · ended"}
                            </div>
                        </div>
                        <span className="muted">→</span>
                    </div>
                </a>
            ))}
        </div>
    );
}

function TreasuryPanel({ communityId, imMember, user }) {
    const [wallet, setWallet] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    const reload = useCallback(async () => {
        setLoading(true); setError(null);
        try {
            const w = await api.get(`/communities/${communityId}/wallet`);
            setWallet(w);
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, [communityId]);
    useEffect(() => { reload(); }, [reload]);

    if (loading) return <div className="muted">Loading treasury…</div>;
    if (error) return <ErrorBanner error={error} />;
    if (!wallet) return null;

    return (
        <div className="stack">
            <div className="card">
                <div className="row" style={{ justifyContent: "space-between" }}>
                    <div>
                        <div className="muted" style={{ fontSize: "0.85rem" }}>Community balance</div>
                        <div style={{ fontSize: "1.8rem", fontWeight: 700, color: "var(--accent)" }}>
                            {parseFloat(wallet.balance).toFixed(2)} <span style={{ fontSize: "0.9rem", color: "var(--text-dim)" }}>credits</span>
                        </div>
                    </div>
                    {imMember && (
                        <button className="btn primary" onClick={() => {
                            const amount = prompt("Propose payment — amount?", "10");
                            if (!amount) return;
                            const pitch = prompt("Pitch / memo for the proposal:", "Pay external vendor");
                            api.post(`/communities/${communityId}/payment-request`,
                                { amount, pitch })
                                .then(() => toast("Payment proposal filed. Community votes next pulse.", "success"))
                                .catch((e) => toast(e.message, "error"));
                        }}>
                            ↗ Propose payment
                        </button>
                    )}
                </div>
            </div>

            <div className="card">
                <div className="bold" style={{ marginBottom: "0.5rem" }}>Recent ledger</div>
                {wallet.recent_entries.length === 0 ? (
                    <div className="muted">No movements yet. Deposits come via webhook; payments and funding flow through proposals.</div>
                ) : (
                    <div className="stack">
                        {wallet.recent_entries.map(e => {
                            const inbound = e.to_wallet === wallet.id;
                            return (
                                <div key={e.id} className="row" style={{ justifyContent: "space-between", padding: "0.4rem 0", borderBottom: "1px solid var(--border)" }}>
                                    <div>
                                        <span style={{ color: inbound ? "var(--accent)" : "var(--danger)", fontWeight: 600 }}>
                                            {inbound ? "↓" : "↑"} {parseFloat(e.amount).toFixed(2)}
                                        </span>
                                        <span className="muted" style={{ fontSize: "0.82rem", marginLeft: 10 }}>
                                            {e.memo || (e.webhook_event ? `webhook: ${e.webhook_event}` : "transfer")}
                                        </span>
                                    </div>
                                    <span className="muted" style={{ fontSize: "0.8rem" }}>
                                        {new Date(e.created_at).toLocaleString()}
                                    </span>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {imMember && (
                <div className="card" style={{ background: "var(--accent-soft)" }}>
                    <div className="muted" style={{ fontSize: "0.9rem" }}>
                        <strong>Money flow</strong>: deposits enter only via authenticated webhook
                        (not a proposal). Parent → child action grants go through <code>Funding</code>
                        proposals. Leaf actions propose <code>Payment</code> to move credits out.
                        Dividends split the wallet across active members.
                    </div>
                </div>
            )}
        </div>
    );
}

function BotConfigPanel({ communityId, user }) {
    // Phase A of the bot UI: load profile if it exists, edit, save.
    // The panel always shows the current state of the bot — including
    // `active` toggle, all persona fields, and the last-turn timestamp.
    const [profile, setProfile] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const [error, setError] = useState(null);

    // Default form values — used both for brand-new bots and as the
    // baseline the edit form snaps back to on Reset.
    const blank = {
        active: true,
        display_name: "",
        orientation: "pragmatist",
        initiative: 5,
        agreeableness: 5,
        goals: "",
        boundaries: "",
        approval_mode: "autonomous",
        turn_interval_seconds: 300,
    };
    const [form, setForm] = useState(blank);

    const reload = useCallback(async () => {
        setLoading(true); setError(null); setMsg(null);
        try {
            const bots = await api.get("/users/me/bots");
            const mine = bots.find(b => b.community_id === communityId);
            if (mine) {
                setProfile(mine);
                setForm({
                    active: mine.active,
                    display_name: mine.display_name || "",
                    orientation: mine.orientation,
                    initiative: mine.initiative,
                    agreeableness: mine.agreeableness,
                    goals: mine.goals || "",
                    boundaries: mine.boundaries || "",
                    approval_mode: mine.approval_mode,
                    turn_interval_seconds: mine.turn_interval_seconds,
                });
            } else {
                setProfile(null);
                setForm(blank);
            }
        } catch (e) { setError(e.message); }
        finally { setLoading(false); }
    }, [communityId]);
    useEffect(() => { reload(); }, [reload]);

    const disable = async () => {
        if (!confirm("Delete this bot? You can re-enable later.")) return;
        try {
            await api._fetch(`/users/me/bots/${communityId}`, { method: "DELETE" });
            setProfile(null);
            setForm(blank);
            setMsg("Bot removed.");
        } catch (err) { setError(err.message); }
    };

    const putSave = async (e) => {
        e?.preventDefault?.();
        setSaving(true); setError(null); setMsg(null);
        try {
            const body = { ...form };
            if (!body.display_name.trim()) body.display_name = null;
            const saved = await api._fetch(`/users/me/bots/${communityId}`, {
                method: "PUT",
                body: JSON.stringify(body),
            });
            setProfile(saved);
            setMsg(profile ? "Saved." : "Bot activated.");
        } catch (err) { setError(err.message); }
        finally { setSaving(false); }
    };

    if (loading) return <div className="muted">Loading bot config…</div>;

    return (
        <form onSubmit={putSave} className="stack">
            <div className="card">
                <h3 style={{ margin: 0 }}>🤖 Your delegated bot</h3>
                <p className="muted" style={{ marginBottom: 0 }}>
                    Configure an AI proxy to act here on your behalf. The bot proposes,
                    supports, and comments <strong>as you</strong> — your user_id is on every
                    action. Toggle off anytime to take back the seat manually.
                </p>
                {profile && profile.last_turn_at && (
                    <div className="muted" style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
                        Last turn: {new Date(profile.last_turn_at).toLocaleString()}
                    </div>
                )}
            </div>

            <div className="card">
                <label className="row" style={{ alignItems: "center", marginBottom: "0.8rem" }}>
                    <input type="checkbox" checked={form.active}
                           onChange={(e) => setForm({ ...form, active: e.target.checked })} />
                    <span className="bold">Active</span>
                    <span className="muted" style={{ fontSize: "0.85rem" }}>
                        When off the bot keeps its config but stops acting.
                    </span>
                </label>

                <label style={{ display: "block", marginBottom: "0.75rem" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Bot name (optional)</div>
                    <input className="input" maxLength={100}
                           placeholder={`${user.user_name}-bot`}
                           value={form.display_name}
                           onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                        How the bot refers to itself in comments. Defaults to "{user.user_name}-bot".
                    </div>
                </label>

                <label style={{ display: "block", marginBottom: "0.75rem" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Orientation</div>
                    <select className="input" value={form.orientation}
                            onChange={(e) => setForm({ ...form, orientation: e.target.value })}>
                        {BOT_ORIENTATIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                    </select>
                </label>

                <Slider label="Initiative" value={form.initiative}
                        onChange={(v) => setForm({ ...form, initiative: v })}
                        hint="Low = mostly observes, high = often proposes." />

                <Slider label="Agreeableness" value={form.agreeableness}
                        onChange={(v) => setForm({ ...form, agreeableness: v })}
                        hint="Low = picky, high = supports most things." />

                <label style={{ display: "block", marginBottom: "0.75rem" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Goals (optional)</div>
                    <textarea className="input" rows={3}
                              placeholder="What should this kibbutz accomplish? e.g. 'Ship an onboarding handbook by May.'"
                              value={form.goals}
                              onChange={(e) => setForm({ ...form, goals: e.target.value })} />
                </label>

                <label style={{ display: "block", marginBottom: "0.75rem" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Boundaries (optional)</div>
                    <textarea className="input" rows={3}
                              placeholder="What should the bot NEVER do? e.g. 'Never propose ThrowOut. Never commit an artifact alone.'"
                              value={form.boundaries}
                              onChange={(e) => setForm({ ...form, boundaries: e.target.value })} />
                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                        Copied verbatim into the bot's prompt as "HARD BOUNDARIES".
                    </div>
                </label>

                <label style={{ display: "block", marginBottom: "0.75rem" }}>
                    <div className="bold" style={{ marginBottom: 4 }}>Turn cadence</div>
                    <select className="input" value={form.turn_interval_seconds}
                            onChange={(e) => setForm({ ...form, turn_interval_seconds: parseInt(e.target.value, 10) })}>
                        <option value={120}>Every 2 minutes (fast)</option>
                        <option value={300}>Every 5 minutes (default)</option>
                        <option value={900}>Every 15 minutes</option>
                        <option value={3600}>Every hour</option>
                        <option value={86400}>Once a day</option>
                    </select>
                </label>

                <div className="row" style={{ justifyContent: "space-between", marginTop: "1rem" }}>
                    {profile
                        ? <button type="button" className="btn ghost" onClick={disable}>Delete bot</button>
                        : <span />}
                    <button type="submit" className="btn primary" disabled={saving}>
                        {saving ? "Saving…" : (profile ? "Save changes" : "Activate bot")}
                    </button>
                </div>
                {msg && <p style={{ color: "var(--accent)" }}>{msg}</p>}
                <ErrorBanner error={error} />
            </div>
        </form>
    );
}

function ProposePage({ communityId, user }) {
    const [ptype, setPtype]       = useState("AddStatement");
    const [text, setText]         = useState("");
    const [pitch, setPitch]       = useState("");
    const [valText, setValText]   = useState("");
    const [valUuid, setValUuid]   = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    // Community context — drives which types are shown + dropdown data
    const [isFinancial, setIsFinancial] = useState(false);
    const [statements, setStatements]   = useState([]);
    const [members, setMembers]         = useState([]);
    const [actions, setActions]         = useState([]);
    const [containers, setContainers]   = useState([]);
    const [artifacts, setArtifacts]     = useState([]);
    const [ctxLoaded, setCtxLoaded]     = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [v, s, m, a] = await Promise.all([
                    api.get(`/communities/${communityId}/variables`),
                    api.get(`/communities/${communityId}/statements`),
                    api.get(`/communities/${communityId}/members`),
                    api.get(`/communities/${communityId}/children`),
                ]);
                const fin = (v?.variables?.Financial || "false");
                setIsFinancial(fin !== "false" && fin !== "");
                setStatements(s || []);
                setMembers(m || []);
                setActions(a || []);
                // Containers + artifacts best-effort (may not exist)
                try {
                    const tree = await api.get(`/artifacts/communities/${communityId}/work_tree`);
                    const conts = Array.isArray(tree) ? tree : [];
                    setContainers(conts);
                    const arts = [];
                    const walk = (c) => {
                        (c.artifacts || []).forEach(a => {
                            arts.push(a);
                            (a.delegated_to || []).forEach(walk);
                        });
                    };
                    conts.forEach(walk);
                    setArtifacts(arts);
                } catch {}
            } catch (e) { setError(e.message); }
            finally { setCtxLoaded(true); }
        })();
    }, [communityId]);

    // Only show types allowed for this community
    const availableTypes = useMemo(
        () => PROPOSAL_CATALOG.filter(t => !t.financialOnly || isFinancial),
        [isFinancial],
    );

    // Group by category for the dropdown
    const groupedTypes = useMemo(() => {
        const by = {};
        for (const t of availableTypes) {
            (by[t.group] = by[t.group] || []).push(t);
        }
        return by;
    }, [availableTypes]);

    const spec = PROPOSAL_CATALOG.find(t => t.value === ptype);

    // Reset the type-specific fields when the type changes
    useEffect(() => {
        setValText("");
        setValUuid("");
    }, [ptype]);

    // Resolve the dropdown items for the chosen type's `pickFrom`
    const pickItems = useMemo(() => {
        if (!spec?.pickFrom) return [];
        switch (spec.pickFrom) {
            case "statements":
                return statements.map(s => ({
                    id: s.id,
                    label: (s.statement_text || "").slice(0, 80) + (s.statement_text?.length > 80 ? "…" : ""),
                }));
            case "members":
                return members
                    .filter(m => m.status === 1)
                    .map(m => ({
                        id: m.user_id,
                        label: m.user_name || m.user_id.slice(0, 8),
                    }));
            case "actions":
                return actions.map(a => ({
                    id: a.id,
                    label: `${a.name}${a.status !== 1 ? " (ended)" : ""}`,
                }));
            case "containers":
                return containers.map(c => ({
                    id: c.id,
                    label: `${c.title || c.id.slice(0, 8)} — ${c.artifacts?.length || 0} artifacts`,
                }));
            case "artifacts":
                return artifacts.map(a => ({
                    id: a.id,
                    label: (a.title || a.id.slice(0, 8)).slice(0, 80),
                }));
            default:
                return [];
        }
    }, [spec, statements, members, actions, containers, artifacts]);

    const canSubmit = (() => {
        if (!spec) return false;
        if (spec.needs?.text && !text.trim()) return false;
        if (spec.needs?.val_text && !valText.trim()) return false;
        if (spec.needs?.val_uuid && !valUuid.trim()) return false;
        return true;
    })();

    const submit = async (e) => {
        e.preventDefault();
        setSubmitting(true); setError(null);
        try {
            const body = {
                user_id: user.user_id,
                proposal_type: ptype,
                proposal_text: text.trim(),
            };
            if (pitch.trim()) body.pitch = pitch.trim();
            if (valText.trim()) body.val_text = valText.trim();
            if (valUuid.trim()) body.val_uuid = valUuid.trim();
            const p = await api.post(`/communities/${communityId}/proposals`, body);
            // Auto-submit so it reaches OutThere
            try { await api._fetch(`/proposals/${p.id}/submit`, { method: "PATCH" }); } catch {}
            // Mark the newly-created proposal so the Kibbutz view can
            // animate it in. Cleared after one render by the consumer.
            try { sessionStorage.setItem("kbz-new-proposal-id", p.id); } catch {}
            toast(`${spec?.label || ptype} filed — scroll down to see it in OutThere`, "success");
            navigate(`#/kibbutz/${communityId}`);
        } catch (err) { setError(err.message); }
        finally { setSubmitting(false); }
    };

    if (!ctxLoaded) return <div className="container">Loading…</div>;

    return (
        <div className="container" style={{ maxWidth: 640 }}>
            <h2>New proposal</h2>
            <p className="muted">Your proposal goes out to the community for support. It advances on the next pulse.</p>
            <form className="stack card" onSubmit={submit}>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Type</div>
                    <select className="input" value={ptype}
                            onChange={(e) => setPtype(e.target.value)}>
                        {PROPOSAL_GROUPS.map(g => (
                            (groupedTypes[g] || []).length > 0 && (
                                <optgroup key={g} label={g}>
                                    {groupedTypes[g].map(t => (
                                        <option key={t.value} value={t.value}>{t.label}</option>
                                    ))}
                                </optgroup>
                            )
                        ))}
                    </select>
                    {spec?.help && (
                        <div className="muted" style={{ fontSize: "0.82rem", marginTop: 4 }}>
                            {spec.help}
                        </div>
                    )}
                </label>

                {spec?.pickFrom && (
                    <label>
                        <div className="bold" style={{ marginBottom: 4 }}>
                            {spec.val_uuid_label || "Target"}
                        </div>
                        {pickItems.length === 0 ? (
                            <div className="muted" style={{ fontSize: "0.85rem" }}>
                                No {spec.pickFrom} available. You may need one to exist first.
                            </div>
                        ) : (
                            <select className="input" required value={valUuid}
                                    onChange={(e) => setValUuid(e.target.value)}>
                                <option value="">— choose —</option>
                                {pickItems.map(it => (
                                    <option key={it.id} value={it.id}>{it.label}</option>
                                ))}
                            </select>
                        )}
                    </label>
                )}

                {spec?.needs?.text !== false && (
                    <label>
                        <div className="bold" style={{ marginBottom: 4 }}>
                            {spec?.text_label || "Description"}
                        </div>
                        <textarea className="input"
                                  rows={spec?.text_rows || 4}
                                  required={!!spec?.needs?.text}
                                  placeholder={spec?.text_placeholder || "What are you proposing?"}
                                  value={text} onChange={(e) => setText(e.target.value)} />
                    </label>
                )}

                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>
                        Pitch
                        <span className="muted" style={{ fontWeight: 400, marginLeft: 6 }}>
                            — why should the community accept this?
                        </span>
                    </div>
                    <textarea className="input"
                              rows={3}
                              placeholder="A short case for accepting this proposal. 1–3 sentences, in your own words."
                              value={pitch} onChange={(e) => setPitch(e.target.value)} />
                </label>

                {spec?.needs?.val_text && (
                    <label>
                        <div className="bold" style={{ marginBottom: 4 }}>
                            {spec.val_text_label || "Value"}
                        </div>
                        <input className="input" required
                               placeholder={spec.val_text_placeholder || ""}
                               value={valText}
                               onChange={(e) => setValText(e.target.value)} />
                    </label>
                )}

                <div className="row" style={{ justifyContent: "flex-end" }}>
                    <a href={`#/kibbutz/${communityId}`} className="btn ghost">Cancel</a>
                    <button className="btn primary" disabled={submitting || !canSubmit}>
                        {submitting ? "Filing…" : "File proposal"}
                    </button>
                </div>
                <ErrorBanner error={error} />
            </form>
        </div>
    );
}

// ── Invite claim ────────────────────────────────────────
function InviteClaimPage({ code, onLoggedIn }) {
    const [preview, setPreview] = useState(null);
    const [email, setEmail] = useState("");
    const [verifyLink, setVerifyLink] = useState(null);
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        api.get(`/invites/${code}`).then(setPreview).catch((e) => setError(e.message));
    }, [code]);

    const submit = async (e) => {
        e.preventDefault();
        setSubmitting(true); setError(null);
        try {
            const r = await api.post("/invites/claim", { invite_code: code, email });
            setVerifyLink(r.verify_link);
        } catch (err) { setError(err.message); }
        finally { setSubmitting(false); }
    };
    const activate = async () => {
        try {
            await api.get(verifyLink);
            await onLoggedIn();
            navigate("#/dashboard");
        } catch (err) { setError(err.message); }
    };

    if (error && !preview) return <div className="container"><div className="card"><ErrorBanner error={error} /></div></div>;
    if (!preview) return <div className="container">Loading invite…</div>;

    return (
        <div className="container" style={{ maxWidth: 480 }}>
            <div className="card">
                {preview.claimed ? (
                    <>
                        <h2 style={{ marginTop: 0 }}>This invite was already used.</h2>
                        <p className="muted">Ask the sender for a fresh one.</p>
                    </>
                ) : verifyLink ? (
                    <>
                        <h2 style={{ marginTop: 0 }}>You're in! 🎉</h2>
                        <p className="muted">Your Membership proposal was filed. Activate your account to enter.</p>
                        <button className="btn primary" onClick={activate}>🔑 Activate &amp; enter</button>
                    </>
                ) : (
                    <>
                        <h2 style={{ marginTop: 0 }}>Join <span style={{ color: "var(--accent)" }}>{preview.community_name}</span>?</h2>
                        <p className="muted">
                            Your membership goes through a community vote — existing members decide whether to admit you.
                        </p>
                        <form className="stack" onSubmit={submit}>
                            <input className="input" type="email" required
                                placeholder="you@example.com"
                                value={email} onChange={(e) => setEmail(e.target.value)} />
                            <button className="btn primary" disabled={submitting || !email}>
                                {submitting ? "Applying…" : "Apply for membership"}
                            </button>
                        </form>
                        <ErrorBanner error={error} />
                    </>
                )}
            </div>
        </div>
    );
}

// ── Profile ─────────────────────────────────────────────
function ProfilePage({ user, onRefresh, onLogout }) {
    const [userName, setUserName] = useState(user.user_name);
    const [about, setAbout] = useState(user.about || "");
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState(null);
    const [error, setError] = useState(null);

    const save = async (e) => {
        e.preventDefault();
        setSaving(true); setError(null); setMsg(null);
        try {
            await api.patch("/users/me", { user_name: userName, about });
            await onRefresh();
            setMsg("Saved.");
        } catch (err) { setError(err.message); }
        finally { setSaving(false); }
    };

    return (
        <div className="container" style={{ maxWidth: 640 }}>
            <h2>Profile</h2>
            <div className="muted" style={{ marginBottom: "1rem" }}>{user.email}</div>
            <form className="stack card" onSubmit={save}>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Display name</div>
                    <input className="input" value={userName} onChange={(e) => setUserName(e.target.value)} />
                </label>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>About (optional)</div>
                    <textarea className="input" rows={4} value={about} onChange={(e) => setAbout(e.target.value)} />
                </label>
                <div className="row" style={{ justifyContent: "space-between" }}>
                    <button type="button" className="btn ghost" onClick={onLogout}>Log out</button>
                    <button className="btn primary" disabled={saving}>{saving ? "Saving…" : "Save"}</button>
                </div>
                {msg && <p style={{ color: "var(--accent)" }}>{msg}</p>}
                <ErrorBanner error={error} />
            </form>

            <ApiTokenManager />
        </div>
    );
}

// ── API tokens ─────────────────────────────────────────
function ApiTokenManager() {
    const [tokens, setTokens] = useState([]);
    const [name, setName] = useState("");
    const [creating, setCreating] = useState(false);
    const [justCreated, setJustCreated] = useState(null);  // raw value, shown once
    const [error, setError] = useState(null);

    const reload = useCallback(async () => {
        try {
            const ts = await api.get("/users/me/tokens");
            setTokens(ts);
        } catch (e) { setError(e.message); }
    }, []);
    useEffect(() => { reload(); }, [reload]);

    const create = async (e) => {
        e.preventDefault();
        setCreating(true); setError(null);
        try {
            const t = await api.post("/users/me/tokens", { name: name.trim() });
            setJustCreated(t.token);   // raw, shown once
            setName("");
            await reload();
        } catch (err) { setError(err.message); }
        finally { setCreating(false); }
    };

    const revoke = async (tid) => {
        if (!confirm("Revoke this token? Bots using it will immediately lose access.")) return;
        try {
            await api._fetch(`/users/me/tokens/${tid}`, { method: "DELETE" });
            await reload();
        } catch (err) { setError(err.message); }
    };

    return (
        <div className="card" style={{ marginTop: "1.25rem" }}>
            <h3 style={{ marginTop: 0 }}>🔑 API tokens</h3>
            <p className="muted">
                Long-lived bearer tokens for external bots — use them with the{" "}
                <a href="#/skills">Kibbutznik MCP server</a> or any agent that can
                set an <code>Authorization: Bearer</code> header. The raw value
                is shown exactly once.
            </p>
            {justCreated && (
                <div className="card" style={{ background: "var(--accent-soft)", marginBottom: "0.8rem" }}>
                    <div className="bold">Save this now — it won't be shown again:</div>
                    <input className="input" readOnly value={justCreated}
                           onClick={(e) => e.target.select()}
                           style={{ fontFamily: "monospace", marginTop: 6 }} />
                    <div className="row" style={{ marginTop: "0.5rem" }}>
                        <button className="btn" onClick={() => navigator.clipboard?.writeText(justCreated)}>
                            📋 Copy
                        </button>
                        <button className="btn ghost" onClick={() => setJustCreated(null)}>Done</button>
                    </div>
                </div>
            )}
            <form className="row" onSubmit={create} style={{ marginBottom: "0.8rem" }}>
                <input className="input" placeholder="Token name (e.g. claude-desktop)"
                       maxLength={80} value={name}
                       onChange={(e) => setName(e.target.value)} style={{ flex: 1 }} />
                <button className="btn primary" disabled={creating || !name.trim()}>
                    {creating ? "Creating…" : "Create token"}
                </button>
            </form>
            {tokens.length === 0
                ? <div className="muted">No tokens yet.</div>
                : <div className="stack">
                    {tokens.map(t => (
                        <div key={t.id} className="card" style={{ padding: "0.6rem 0.9rem" }}>
                            <div className="row" style={{ justifyContent: "space-between" }}>
                                <div>
                                    <div className="bold">{t.name || "(unnamed)"}</div>
                                    <div className="muted" style={{ fontSize: "0.8rem" }}>
                                        created {new Date(t.created_at).toLocaleDateString()} ·
                                        {" "}expires {new Date(t.expires_at).toLocaleDateString()}
                                    </div>
                                </div>
                                <button className="btn ghost" onClick={() => revoke(t.id)}>Revoke</button>
                            </div>
                        </div>
                    ))}
                </div>}
            <ErrorBanner error={error} />
        </div>
    );
}

// ── Skills page (public, no auth required to read) ─────
function SkillsPage() {
    return (
        <div className="container" style={{ maxWidth: 760 }}>
            <h2>Bring your own bot</h2>
            <p className="muted">
                Kibbutznik exposes its governance actions as tools your AI agent
                can use directly — Claude Desktop, Claude Code, Cursor, ChatGPT,
                LangChain, or your own harness. You run the bot, you pay your
                own LLM bill, we just handle the governance.
            </p>

            <div className="card" style={{ marginBottom: "1rem" }}>
                <h3 style={{ marginTop: 0 }}>1. Get an API token</h3>
                <p className="muted" style={{ margin: 0 }}>
                    Sign in, go to{" "}
                    <a href="#/profile">Profile</a> → API tokens → Create token.
                    The value is shown exactly once — paste it into your bot's
                    config immediately.
                </p>
            </div>

            <div className="card" style={{ marginBottom: "1rem" }}>
                <h3 style={{ marginTop: 0 }}>2a. MCP server (Claude Desktop, Claude Code, Cursor, Zed, Goose, …)</h3>
                <p className="muted">
                    The <code>kibbutznik-mcp</code> Python package exposes 9 typed
                    tools to any MCP host. The agent reasons locally; the server
                    is a thin, authenticated wrapper over our HTTP API.
                </p>
                <pre style={{ background: "#0f1a1a", color: "#9cd", padding: "0.8rem", borderRadius: 6, overflow: "auto", fontSize: "0.85rem" }}>
{`pip install kibbutznik-mcp
# add to your MCP host config:
{
  "mcpServers": {
    "kibbutznik": {
      "command": "kibbutznik-mcp",
      "env": { "KIBBUTZNIK_API_TOKEN": "kbz_..." }
    }
  }
}`}
                </pre>
                <div className="row" style={{ marginTop: "0.5rem", flexWrap: "wrap", gap: "0.5rem" }}>
                    <a className="btn" href="https://github.com/kibbutznik/kibbutznik-mcp" target="_blank" rel="noopener">GitHub</a>
                    <a className="btn ghost" href="https://modelcontextprotocol.io/" target="_blank" rel="noopener">What is MCP?</a>
                </div>
            </div>

            <div className="card" style={{ marginBottom: "1rem" }}>
                <h3 style={{ marginTop: 0 }}>2b. Claude Code skill (markdown)</h3>
                <p className="muted">
                    If your host doesn't speak MCP, drop our <code>SKILL.md</code>
                    into your Claude Code skills dir. It tells the agent how to
                    hit our HTTP API directly with <code>curl</code>.
                </p>
                <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                    <a className="btn primary" href="/app/skills/SKILL.md" download>
                        📄 Download SKILL.md
                    </a>
                    <a className="btn ghost" href="/app/skills/SKILL.md" target="_blank" rel="noopener">
                        View in browser
                    </a>
                </div>
            </div>

            <div className="card" style={{ marginBottom: "1rem" }}>
                <h3 style={{ marginTop: 0 }}>2c. Anything else (OpenAI Custom GPT, LangChain, autogen, curl, …)</h3>
                <p className="muted">
                    Point your framework at the OpenAPI spec. Auth with{" "}
                    <code>Authorization: Bearer $KIBBUTZNIK_API_TOKEN</code>.
                </p>
                <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                    <a className="btn" href="/kbz/openapi.json" target="_blank" rel="noopener">
                        📜 openapi.json
                    </a>
                    <a className="btn ghost" href="/kbz/docs" target="_blank" rel="noopener">
                        Swagger UI
                    </a>
                </div>
            </div>

            <div className="muted" style={{ fontSize: "0.85rem", marginTop: "1.5rem" }}>
                Heads up: every bot write is signed as your user_id — the server
                refuses requests where <code>body.user_id</code> doesn't match
                your token. You can revoke any token anytime from{" "}
                <a href="#/profile">Profile</a>.
            </div>
        </div>
    );
}

// ── Root app ────────────────────────────────────────────
function App() {
    const { user, loaded, refresh, logout } = useAuth();
    const route = useHashRoute();

    const content = useMemo(() => {
        if (!loaded) return <div className="container">Loading…</div>;
        const [root, arg, sub] = route.segments;
        if (route.path === "/" || route.path === "") return <LandingPage user={user} />;
        if (route.path === "/login") return <LoginPage onLoggedIn={refresh} />;
        if (route.path === "/browse") return <BrowsePage user={user} />;
        if (route.path === "/skills") return <SkillsPage />;
        if (root === "invite" && arg) return <InviteClaimPage code={arg} onLoggedIn={refresh} />;
        if (root === "kibbutz" && arg === "new") {
            if (!user) { navigate("#/login"); return null; }
            return <CreateKibbutzPage user={user} />;
        }
        if (root === "kibbutz" && arg && sub === "propose") {
            if (!user) { navigate("#/login"); return null; }
            return <ProposePage communityId={arg} user={user} />;
        }
        if (root === "kibbutz" && arg) {
            return <KibbutzPage communityId={arg} user={user} onRefreshMembership={refresh} />;
        }
        if (route.path === "/dashboard") {
            if (!user) { navigate("#/login"); return null; }
            return <DashboardPage user={user} />;
        }
        if (route.path === "/profile") {
            if (!user) { navigate("#/login"); return null; }
            return <ProfilePage user={user} onRefresh={refresh}
                        onLogout={async () => { await logout(); navigate("#/"); }} />;
        }
        return <div className="container"><p>Page not found. <a href="#/">Home</a></p></div>;
    }, [loaded, route, user, refresh, logout]);

    return (
        <>
            <Header user={user} onLogout={async () => { await logout(); navigate("#/"); }} />
            {content}
            <footer className="app-footer">
                Kibbutznik · <a href="/kbz/viewer/">AI simulation</a> · <a href="/">landing</a>
            </footer>
            <ToastHost />
        </>
    );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
