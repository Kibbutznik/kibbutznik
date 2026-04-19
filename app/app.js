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
    const [sending, setSending] = useState(false);
    const [devLink, setDevLink] = useState(null);
    const [sent, setSent] = useState(false);
    const [error, setError] = useState(null);

    const submit = async (e) => {
        e.preventDefault();
        setSending(true); setError(null); setDevLink(null); setSent(false);
        try {
            const r = await api.post("/auth/request-magic-link", { email });
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
                        Check your inbox — the link signs you in for 7 days.
                    </p>
                )}
                <ErrorBanner error={error} />
            </div>
        </div>
    );
}

// ── Dashboard ───────────────────────────────────────────
function DashboardPage({ user }) {
    const [memberships, setMemberships] = useState([]);
    const [pendingApps, setPendingApps] = useState([]);
    const [sentInvites, setSentInvites] = useState([]);
    const [bots, setBots] = useState([]);
    const [wallet, setWallet] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const [m, a, s, b, w] = await Promise.all([
                    api.get("/users/me/memberships"),
                    api.get("/users/me/pending-applications"),
                    api.get("/users/me/sent-invites"),
                    api.get("/users/me/bots"),
                    api.get("/users/me/wallet").catch(() => null),
                ]);
                if (cancelled) return;
                setMemberships(m);
                setPendingApps(a);
                setSentInvites(s);
                setBots(b);
                setWallet(w);
            } catch (e) { setError(e.message); }
            finally { if (!cancelled) setLoading(false); }
        })();
        return () => { cancelled = true; };
    }, []);

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

            <section style={{ marginBottom: "1.5rem" }}>
                <h3>Your kibbutzim</h3>
                {loading ? <div className="muted">Loading…</div>
                 : memberships.length === 0 ? (
                    <Empty title="You're not a member of any kibbutz yet">
                        Create one above, or <a href="#/browse">apply to join an existing one</a>.
                    </Empty>
                ) : (
                    <div className="stack">
                        {memberships.map(m => (
                            <a key={m.community_id} href={`#/kibbutz/${m.community_id}`}
                                className="card" style={{ textDecoration: "none", color: "inherit", display: "block" }}>
                                <div className="row" style={{ justifyContent: "space-between" }}>
                                    <div>
                                        <div className="bold">{m.community_name}</div>
                                        <div className="muted">Joined {new Date(m.joined_at).toLocaleDateString()} · seniority {m.seniority}</div>
                                    </div>
                                    <span className="pill">member</span>
                                </div>
                            </a>
                        ))}
                    </div>
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
        } catch (e) { alert(e.message); }
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

function KibbutzPage({ communityId, user, onRefreshMembership }) {
    const [community, setCommunity] = useState(null);
    const [members, setMembers] = useState([]);
    const [proposals, setProposals] = useState([]);
    const [statements, setStatements] = useState([]);
    const [tab, setTab] = useState("proposals");
    const [error, setError] = useState(null);
    const [inviteUrl, setInviteUrl] = useState(null);
    const [applyBusy, setApplyBusy] = useState(false);

    const imMember = useMemo(
        () => user && members.some(m => m.user_id === user.user_id),
        [user, members],
    );

    const [variables, setVariables] = useState({});
    const reload = useCallback(async () => {
        setError(null);
        try {
            const [c, m, p, s, v] = await Promise.all([
                api.get(`/communities/${communityId}`),
                api.get(`/communities/${communityId}/members`),
                api.get(`/communities/${communityId}/proposals`),
                api.get(`/communities/${communityId}/statements`),
                api.get(`/communities/${communityId}/variables`),
            ]);
            setCommunity(c);
            setMembers(m);
            setProposals(p);
            setStatements(s);
            setVariables(v.variables || {});
        } catch (e) { setError(e.message); }
    }, [communityId]);
    useEffect(() => { reload(); }, [reload]);

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
            alert("Application filed. Check your dashboard for progress.");
            onRefreshMembership?.();
        } catch (e) { alert(e.message); }
        finally { setApplyBusy(false); }
    };

    const createInvite = async () => {
        try {
            const r = await api.post(`/communities/${communityId}/invites`, {});
            setInviteUrl(window.location.origin + "/app/#/invite/" + r.code);
        } catch (e) { alert(e.message); }
    };

    if (!community) {
        return <div className="container">{error ? <ErrorBanner error={error} /> : "Loading…"}</div>;
    }

    const sortedProposals = [...proposals].sort((a, b) => {
        const order = { OutThere: 0, OnTheAir: 1, Accepted: 2, Rejected: 3, Canceled: 4, Draft: 5 };
        return (order[a.proposal_status] ?? 9) - (order[b.proposal_status] ?? 9);
    });

    return (
        <div className="container">
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
                    const base = ["proposals", "members", "statements"];
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
                        {t === "bot" ? "🤖 My bot" : t === "treasury" ? "💰 Treasury" : t[0].toUpperCase() + t.slice(1)}
                    </button>
                ))}
            </div>
            {tab === "proposals" && (
                sortedProposals.length === 0 ? <Empty title="No proposals yet">Be the first to propose something.</Empty> :
                <div className="stack">
                    {sortedProposals.map(p => (
                        <ProposalCard key={p.id} proposal={p} imMember={imMember} user={user} onChanged={reload} />
                    ))}
                </div>
            )}
            {tab === "members" && (
                <div className="stack">
                    {members.map(m => (
                        <div key={m.user_id} className="card">
                            <div className="bold">{m.user_name || m.user_id.slice(0, 8)}</div>
                            <div className="muted">seniority {m.seniority}</div>
                        </div>
                    ))}
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
            {tab === "bot" && imMember && (
                <BotConfigPanel communityId={communityId} user={user} />
            )}
            {tab === "treasury" && isFinancial && (
                <TreasuryPanel communityId={communityId} imMember={imMember} user={user} />
            )}
        </div>
    );
}

function ProposalCard({ proposal, imMember, user, onChanged }) {
    const [supporting, setSupporting] = useState(false);
    const color = PROPOSAL_STATUS_COLORS[proposal.proposal_status] || "var(--text-dim)";
    const canAct = imMember && (proposal.proposal_status === "OutThere" || proposal.proposal_status === "OnTheAir");

    const support = async () => {
        setSupporting(true);
        try {
            await api.post(`/proposals/${proposal.id}/support`, { user_id: user.user_id });
            onChanged?.();
        } catch (e) { alert(e.message); }
        finally { setSupporting(false); }
    };
    return (
        <div className="card">
            <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                    <div className="row" style={{ marginBottom: 4 }}>
                        <span className="pill" style={{ background: `${color}22`, color }}>{proposal.proposal_type}</span>
                        <span className="pill" style={{ background: "transparent", border: `1px solid ${color}`, color }}>
                            {proposal.proposal_status}
                        </span>
                        <span className="muted" style={{ fontSize: "0.75rem" }}>age {proposal.age} · support {proposal.support_count}</span>
                    </div>
                    <div>{proposal.val_text || proposal.proposal_text || <span className="muted">(untitled)</span>}</div>
                    {proposal.val_text && proposal.proposal_text && proposal.val_text !== proposal.proposal_text && (
                        <div className="muted" style={{ marginTop: 4, fontSize: "0.88rem" }}>{proposal.proposal_text}</div>
                    )}
                </div>
                {canAct && (
                    <button className="btn" disabled={supporting} onClick={support}>
                        {supporting ? "…" : "👍 Support"}
                    </button>
                )}
            </div>
        </div>
    );
}

// ── Propose form ────────────────────────────────────────
const HUMAN_PROPOSAL_TYPES = [
    { value: "AddStatement",    label: "Add a statement (community rule)" },
    { value: "RemoveStatement", label: "Remove a statement" },
    { value: "ReplaceStatement", label: "Replace an existing statement" },
    { value: "ChangeVariable",  label: "Change a governance variable" },
    { value: "ThrowOut",        label: "Throw out a member" },
];

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
                                .then(() => alert("Payment proposal filed. Community votes next pulse."))
                                .catch((e) => alert(e.message));
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
    const [ptype, setPtype]     = useState("AddStatement");
    const [text, setText]       = useState("");
    const [valText, setValText] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    const submit = async (e) => {
        e.preventDefault();
        setSubmitting(true); setError(null);
        try {
            const body = {
                user_id: user.user_id,
                proposal_type: ptype,
                proposal_text: text.trim(),
            };
            if (valText.trim()) body.val_text = valText.trim();
            const p = await api.post(`/communities/${communityId}/proposals`, body);
            // Submit it so it reaches OutThere
            try { await api._fetch(`/proposals/${p.id}/submit`, { method: "PATCH" }); } catch {}
            navigate(`#/kibbutz/${communityId}`);
        } catch (err) { setError(err.message); }
        finally { setSubmitting(false); }
    };

    return (
        <div className="container" style={{ maxWidth: 640 }}>
            <h2>New proposal</h2>
            <p className="muted">Your proposal goes out to the community for support. It advances on the next pulse.</p>
            <form className="stack card" onSubmit={submit}>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Type</div>
                    <select className="input" value={ptype} onChange={(e) => setPtype(e.target.value)}>
                        {HUMAN_PROPOSAL_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                </label>
                <label>
                    <div className="bold" style={{ marginBottom: 4 }}>Description</div>
                    <textarea className="input" rows={4} required
                              placeholder="What are you proposing, and why?"
                              value={text} onChange={(e) => setText(e.target.value)} />
                </label>
                {ptype === "ChangeVariable" && (
                    <label>
                        <div className="bold" style={{ marginBottom: 4 }}>New value</div>
                        <input className="input" placeholder="e.g. 60"
                               value={valText} onChange={(e) => setValText(e.target.value)} />
                    </label>
                )}
                <div className="row" style={{ justifyContent: "flex-end" }}>
                    <a href={`#/kibbutz/${communityId}`} className="btn ghost">Cancel</a>
                    <button className="btn primary" disabled={submitting || !text.trim()}>
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
        </>
    );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
