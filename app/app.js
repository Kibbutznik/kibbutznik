/*
 * KBZ Kibbutz — Phase A starter shell.
 *
 * What's here now:
 *  - Hash-based router (#/, #/login, #/dashboard, #/invite/:code)
 *  - API helper that talks to the SAME backend as the simulation
 *    (mounts at /kbz/ when behind nginx, / in dev)
 *  - Landing page + sign-in form wired to /auth/request-magic-link
 *  - Dashboard stub: greets the signed-in user, shows "coming soon"
 *    tiles for each Phase B screen
 *
 * Phase B (next commit) fills in the real pages.
 */

const { useState, useEffect, useCallback, useMemo } = React;

// Detect API base path. At kibbutznik.org we're served via nginx which
// proxies /kbz/ to the FastAPI backend. In dev (python http.server on
// 8090) we hit the backend cross-origin on :8000 via CORS.
const API_BASE = (() => {
    const { pathname, origin } = window.location;
    if (pathname.startsWith("/app/")) return "/kbz";  // prod behind nginx
    return "";                                          // local dev; same-origin or CORS
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
            throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
        }
        return body;
    },
    get(p) { return this._fetch(p); },
    post(p, body) { return this._fetch(p, { method: "POST", body: JSON.stringify(body || {}) }); },
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

// ── Auth hook — mirror of /auth/me ──────────────────────
function useAuth() {
    const [user, setUser] = useState(null);
    const [loaded, setLoaded] = useState(false);
    const refresh = useCallback(async () => {
        try {
            const r = await api.get("/auth/me");
            setUser(r.user || null);
        } catch {
            setUser(null);
        } finally {
            setLoaded(true);
        }
    }, []);
    useEffect(() => { refresh(); }, [refresh]);
    const logout = useCallback(async () => {
        try { await api.post("/auth/logout", {}); } catch {}
        setUser(null);
    }, []);
    return { user, loaded, refresh, logout };
}

// ── Header ──────────────────────────────────────────────
function Header({ user, onLogout }) {
    return (
        <header className="app-header">
            <a href="#/" className="brand">KBZ Kibbutz</a>
            <div className="row">
                {user ? (
                    <>
                        <span className="muted">👤 {user.user_name}</span>
                        <a href="#/dashboard" className="btn ghost">Dashboard</a>
                        <button className="btn ghost" onClick={onLogout}>Log out</button>
                    </>
                ) : (
                    <a href="#/login" className="btn primary">Sign in</a>
                )}
            </div>
        </header>
    );
}

// ── Landing ─────────────────────────────────────────────
function LandingPage() {
    return (
        <>
            <section className="hero">
                <h1>Run your community by pulse, not politics.</h1>
                <p>
                    KBZ Kibbutz is a shared-decision tool for groups who want to move together
                    without voting everything to death. Propose, support, pulse, and watch
                    decisions settle. Built on the same pulse engine that runs our AI simulation
                    at{" "}
                    <a href="/kbz/viewer/">kibbutznik.org/kbz/viewer</a>.
                </p>
                <div className="row" style={{ justifyContent: "center" }}>
                    <a href="#/login" className="btn primary">Get started</a>
                    <a href="#/browse" className="btn">Browse public kibbutzim</a>
                </div>
            </section>
            <div className="container">
                <div className="features">
                    <div className="feature">
                        <div className="feature-title">No passwords</div>
                        <div className="muted">Sign in with a magic link. That's it.</div>
                    </div>
                    <div className="feature">
                        <div className="feature-title">Proposal-gated everything</div>
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
    const [error, setError] = useState(null);

    const submit = async (e) => {
        e.preventDefault();
        setSending(true); setError(null); setDevLink(null);
        try {
            const r = await api.post("/auth/request-magic-link", { email });
            if (r.link) setDevLink(r.link);
            else setError("Check your inbox for a sign-in link.");
        } catch (err) { setError(err.message); }
        finally { setSending(false); }
    };

    const verify = async () => {
        try {
            await api.get(devLink);          // consumes token, sets cookie
            await onLoggedIn();              // refresh /auth/me
            navigate("#/dashboard");
        } catch (err) { setError(err.message); }
    };

    return (
        <div className="container" style={{ maxWidth: 480 }}>
            <div className="card">
                <h2 style={{ marginTop: 0 }}>Sign in</h2>
                <p className="muted">
                    Enter your email and we'll send you a one-time sign-in link.
                    No passwords.
                </p>
                {!devLink && (
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
                        <p className="muted">
                            Dev-mode link (production will email this instead):
                        </p>
                        <button className="btn primary" onClick={verify}>
                            🔑 Use magic link
                        </button>
                    </div>
                )}
                {error && <p style={{ color: "var(--danger)", marginTop: "0.8rem" }}>{error}</p>}
            </div>
        </div>
    );
}

// ── Dashboard (Phase A stub) ────────────────────────────
function DashboardPage({ user }) {
    return (
        <div className="container">
            <h2>Welcome back, {user.user_name}</h2>
            <p className="muted">
                Phase A placeholder — the real dashboard (your kibbutzim, pending
                invites, pending Membership proposals) lands in Phase B.
            </p>
            <div className="features">
                <div className="feature">
                    <div className="feature-title">My Kibbutzim</div>
                    <div className="muted">Coming next commit.</div>
                </div>
                <div className="feature">
                    <div className="feature-title">Pending Invites</div>
                    <div className="muted">Coming next commit.</div>
                </div>
                <div className="feature">
                    <div className="feature-title">Applications in Flight</div>
                    <div className="muted">Membership proposals you've filed but haven't been voted on yet.</div>
                </div>
                <div className="feature">
                    <div className="feature-title"><a href="#/kibbutz/new">+ Create a Kibbutz</a></div>
                    <div className="muted">Start a new community.</div>
                </div>
            </div>
        </div>
    );
}

// ── Invite claim (reuses /invites/claim) ────────────────
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

    if (error) return <div className="container"><div className="card"><p style={{ color: "var(--danger)" }}>{error}</p></div></div>;
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
                        <p className="muted">
                            A Membership proposal was filed on your behalf. Click to activate
                            your account and enter the community.
                        </p>
                        <button className="btn primary" onClick={activate}>🔑 Activate &amp; enter</button>
                    </>
                ) : (
                    <>
                        <h2 style={{ marginTop: 0 }}>Join <span style={{ color: "var(--accent)" }}>{preview.community_name}</span>?</h2>
                        <p className="muted">
                            Your membership goes through a community vote — existing members decide
                            whether to admit you.
                        </p>
                        <form className="stack" onSubmit={submit}>
                            <input className="input" type="email" required
                                placeholder="you@example.com"
                                value={email} onChange={(e) => setEmail(e.target.value)} />
                            <button className="btn primary" disabled={submitting || !email}>
                                {submitting ? "Applying…" : "Apply for membership"}
                            </button>
                        </form>
                    </>
                )}
            </div>
        </div>
    );
}

// ── Browse (Phase A stub, Phase B fills in) ─────────────
function BrowsePage() {
    return (
        <div className="container">
            <h2>Public Kibbutzim</h2>
            <p className="muted">
                Phase B will list public kibbutzim here. For now you can peek at the
                AI-agent-run simulation at{" "}
                <a href="/kbz/viewer/">/kbz/viewer/</a>.
            </p>
        </div>
    );
}

function CreateKibbutzPage() {
    return (
        <div className="container">
            <h2>Create a Kibbutz</h2>
            <p className="muted">Phase B — the create-a-kibbutz form lands in the next commit.</p>
        </div>
    );
}

// ── Root app ────────────────────────────────────────────
function App() {
    const { user, loaded, refresh, logout } = useAuth();
    const route = useHashRoute();

    const content = useMemo(() => {
        if (!loaded) return <div className="container">Loading…</div>;
        const [root, arg] = route.segments;
        if (route.path === "/" || route.path === "") return <LandingPage />;
        if (route.path === "/login") return <LoginPage onLoggedIn={refresh} />;
        if (route.path === "/browse") return <BrowsePage />;
        if (root === "invite" && arg) return <InviteClaimPage code={arg} onLoggedIn={refresh} />;
        if (root === "kibbutz" && arg === "new") return <CreateKibbutzPage />;
        if (route.path === "/dashboard") {
            if (!user) { navigate("#/login"); return null; }
            return <DashboardPage user={user} />;
        }
        return <div className="container"><p>Page not found. <a href="#/">Home</a></p></div>;
    }, [loaded, route, user, refresh]);

    return (
        <>
            <Header user={user} onLogout={async () => { await logout(); navigate("#/"); }} />
            {content}
            <footer className="app-footer">
                KBZ Kibbutz · built on the pulse engine ·{" "}
                <a href="/kbz/viewer/">watch the AI simulation</a> ·{" "}
                <a href="/">back to kibbutznik.org</a>
            </footer>
        </>
    );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
