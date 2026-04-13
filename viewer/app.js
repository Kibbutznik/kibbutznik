const { useState, useEffect, useRef, useCallback, useMemo } = React;

// Agent color mapping
const AGENT_COLORS = {};
let colorIdx = 0;
function agentColor(name) {
    if (!AGENT_COLORS[name]) {
        AGENT_COLORS[name] = colorIdx++;
    }
    return `agent-color-${AGENT_COLORS[name] % 6}`;
}

// Detect base path — works both at root (/viewer/) and under a prefix (/kbz/viewer/)
const BASE = window.location.pathname.split('/viewer')[0] || '';

// API helpers with caching
const _cache = {};
const API = {
    async get(path) {
        const res = await fetch(BASE + path);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async getCached(path, ttl = 5000) {
        const now = Date.now();
        if (_cache[path] && now - _cache[path].time < ttl) {
            return _cache[path].data;
        }
        const data = await this.get(path);
        _cache[path] = { data, time: now };
        return data;
    },
    async post(path, body) {
        const res = await fetch(BASE + path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async patch(path, body) {
        const res = await fetch(BASE + path, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
    async delete(path) {
        const res = await fetch(BASE + path, { method: "DELETE" });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
    },
};

// Normalize the variables API response to [{name, value}, ...]
// The endpoint returns {community_id, variables: {name: value, ...}}
function parseVariables(data) {
    if (!data) return [];
    if (Array.isArray(data)) return data;
    const vars = data.variables || data;
    if (typeof vars === "object" && !Array.isArray(vars)) {
        return Object.entries(vars).map(([name, value]) => ({ name, value }));
    }
    return [];
}

function formatTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString();
}

function truncate(s, n = 80) {
    if (!s) return "";
    return s.length > n ? s.slice(0, n) + "..." : s;
}

/** For artifact proposals, val_text (title) is the meaningful text when proposal_text is empty. */
function proposalDisplayText(p) {
    const ARTIFACT_TYPES = ["CreateArtifact","EditArtifact","DelegateArtifact","CommitArtifact","RemoveArtifact"];
    if (p.proposal_text) return p.proposal_text;
    if (ARTIFACT_TYPES.includes(p.proposal_type)) return p.val_text || `(${p.proposal_type})`;
    return "";
}

/**
 * React hook that resolves a human-readable card title for a proposal.
 * For types that carry UUIDs (DelegateArtifact, JoinAction, etc.), fetches
 * the real names asynchronously using the cached API.
 */
function useProposalCardTitle(p) {
    const [title, setTitle] = React.useState(() => proposalDisplayText(p) || p.proposal_type);
    React.useEffect(() => {
        if (!p) return;
        const pt = p.proposal_type;
        let cancelled = false;
        async function resolve() {
            try {
                if (pt === "DelegateArtifact") {
                    // val_uuid = artifact id, val_text = target action community id
                    let artName = p.val_uuid ? p.val_uuid.slice(0, 8) : "?";
                    let actionName = p.val_text ? p.val_text.slice(0, 8) : "?";
                    try { const h = await API.getCached(`/artifacts/${p.val_uuid}/history`); if (h?.length) artName = h[h.length-1].title || artName; } catch {}
                    try { const c = await API.getCached(`/communities/${p.val_text}`); actionName = c.name || actionName; } catch {}
                    if (!cancelled) setTitle(`Delegate "${truncate(artName,30)}" → ${truncate(actionName,25)}`);
                } else if (pt === "EditArtifact" || pt === "RemoveArtifact") {
                    let artName = p.val_uuid ? p.val_uuid.slice(0, 8) : "?";
                    try { const h = await API.getCached(`/artifacts/${p.val_uuid}/history`); if (h?.length) artName = h[h.length-1].title || artName; } catch {}
                    if (!cancelled) setTitle(`${pt === "EditArtifact" ? "Edit" : "Remove"}: ${truncate(artName, 45)}`);
                } else if (pt === "JoinAction" || pt === "EndAction") {
                    let commName = p.val_uuid ? p.val_uuid.slice(0, 8) : "?";
                    try { const c = await API.getCached(`/communities/${p.val_uuid}`); commName = c.name || commName; } catch {}
                    if (!cancelled) setTitle(`${pt === "JoinAction" ? "Join" : "End"}: ${truncate(commName, 45)}`);
                } else if (pt === "CommitArtifact") {
                    let contName = "Container";
                    try { const c = await API.getCached(`/artifacts/containers/${p.val_uuid}`); contName = c?.container?.title || contName; } catch {}
                    if (!cancelled) setTitle(`Commit: ${truncate(contName, 45)}`);
                } else if (pt === "CreateArtifact") {
                    if (!cancelled) setTitle(p.val_text || p.proposal_text || "Create Artifact");
                }
            } catch {}
        }
        resolve();
        return () => { cancelled = true; };
    }, [p?.id]);
    return title;
}

// ── EntityLink ─────────────────────────────────────────
function EntityLink({ type, id, label, openDetail }) {
    return (
        <span
            className="entity-link"
            onClick={(e) => { e.stopPropagation(); openDetail(type, id, label); }}
        >
            {label || id?.slice(0, 8) || "?"}
        </span>
    );
}

// ── LinkedDetails — makes event details clickable when ref_id is present ──
function LinkedDetails({ details, refId, refType, openDetail }) {
    if (!details) return null;
    if (refId && openDetail) {
        const type = refType || "proposal";
        return (
            <span className="entity-link" onClick={(e) => { e.stopPropagation(); openDetail(type, refId, details.slice(0, 30)); }}>
                {details}
            </span>
        );
    }
    return <span>{details}</span>;
}

// ── Breadcrumbs ────────────────────────────────────────
function Breadcrumbs({ stack, popToIndex }) {
    return (
        <div className="breadcrumbs">
            {stack.map((item, i) => (
                <span key={i}>
                    {i > 0 && <span className="breadcrumb-sep">&rsaquo;</span>}
                    <span
                        className={`breadcrumb-item ${i === stack.length - 1 ? "current" : ""}`}
                        onClick={() => i < stack.length - 1 && popToIndex(i)}
                    >
                        {item.label || `${item.type} ${(item.id || "").slice(0, 8)}`}
                    </span>
                </span>
            ))}
        </div>
    );
}

// ── Detail Panel (sliding overlay) ─────────────────────
function DetailPanel({ stack, popToIndex, closeDetail, openDetail, agents, agentsByUserId, communityId, events, bbUserId }) {
    if (stack.length === 0) return null;
    const current = stack[stack.length - 1];

    useEffect(() => {
        function onKey(e) { if (e.key === "Escape") closeDetail(); }
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [closeDetail]);

    let content;
    switch (current.type) {
        case "proposal":
            content = <ProposalDetail id={current.id} openDetail={openDetail} agentsByUserId={agentsByUserId} communityId={communityId} bbUserId={bbUserId} />;
            break;
        case "user":
            content = <MemberDetail id={current.id} openDetail={openDetail} agents={agents} agentsByUserId={agentsByUserId} communityId={communityId} events={events} />;
            break;
        case "community":
            content = <CommunityDetail id={current.id} openDetail={openDetail} agentsByUserId={agentsByUserId} />;
            break;
        case "pulse":
            content = <PulseDetail id={current.id} openDetail={openDetail} agentsByUserId={agentsByUserId} communityId={communityId} />;
            break;
        case "statement":
            content = <StatementDetail id={current.id} openDetail={openDetail} communityId={communityId} agentsByUserId={agentsByUserId} />;
            break;
        case "variable":
            content = <VariableDetail id={current.id} openDetail={openDetail} agentsByUserId={agentsByUserId} />;
            break;
        default:
            content = <div className="empty-state">Unknown entity type: {current.type}</div>;
    }

    return (
        <React.Fragment>
            <div className="detail-panel-backdrop" onClick={closeDetail}></div>
            <div className="detail-panel">
                <div className="detail-panel-header">
                    <Breadcrumbs stack={stack} popToIndex={popToIndex} />
                    <button className="detail-close-btn" onClick={closeDetail}>&times;</button>
                </div>
                <div className="detail-panel-body">
                    {content}
                </div>
            </div>
        </React.Fragment>
    );
}

// ── ProposalDetail ─────────────────────────────────────

/** Resolve val_uuid and val_text to human-readable names (artifact title, community name, member name). */
async function resolveProposalRefs(proposal, agentsByUserId) {
    const result = { valUuid: "", valText: "" };
    if (!proposal) return result;
    const pt = proposal.proposal_type;

    // Resolve val_uuid → name
    if (proposal.val_uuid) {
        // Member names
        if (["Membership", "ThrowOut"].includes(pt)) {
            const agent = agentsByUserId?.[proposal.val_uuid];
            if (agent) result.valUuid = agent.name;
            else try { const u = await API.getCached(`/users/${proposal.val_uuid}`); result.valUuid = u.name; } catch {}
        }
        // Artifact title (EditArtifact, RemoveArtifact, DelegateArtifact)
        else if (["EditArtifact", "RemoveArtifact", "DelegateArtifact"].includes(pt)) {
            try { const hist = await API.getCached(`/artifacts/${proposal.val_uuid}/history`); if (hist.length) result.valUuid = hist[hist.length-1].title || "(untitled)"; } catch {}
        }
        // Container (CreateArtifact, CommitArtifact) — show container title
        else if (["CreateArtifact", "CommitArtifact"].includes(pt)) {
            try { const c = await API.getCached(`/artifacts/containers/${proposal.val_uuid}`); result.valUuid = c?.container?.title || "Container"; } catch {}
        }
        // Action (JoinAction, EndAction) — show community name
        else if (["JoinAction", "EndAction"].includes(pt)) {
            try { const c = await API.getCached(`/communities/${proposal.val_uuid}`); result.valUuid = c.name || c.description || proposal.val_uuid.slice(0, 12); } catch {}
        }
        // Statement (RemoveStatement, ReplaceStatement)
        else if (["RemoveStatement", "ReplaceStatement"].includes(pt)) {
            try { const s = await API.getCached(`/statements/${proposal.val_uuid}`); result.valUuid = truncate(s.statement_text, 40); } catch {}
        }
    }

    // Resolve val_text → name (for DelegateArtifact: val_text is the target action community_id)
    if (proposal.val_text && pt === "DelegateArtifact") {
        try { const c = await API.getCached(`/communities/${proposal.val_text}`); result.valText = c.name || c.description || proposal.val_text.slice(0, 12); } catch {}
    }
    // Resolve val_text for CommitArtifact: JSON array of artifact UUIDs → numbered titles
    if (proposal.val_text && pt === "CommitArtifact") {
        try {
            const ids = JSON.parse(proposal.val_text);
            if (Array.isArray(ids)) {
                const titles = [];
                for (const id of ids) {
                    try { const hist = await API.getCached(`/artifacts/${id}/history`); titles.push(hist.length ? hist[hist.length-1].title || id.slice(0,8) : id.slice(0,8)); } catch { titles.push(id.slice(0,8)); }
                }
                result.valText = titles.map((t,i) => `${i+1}. ${t}`).join('\n');
            }
        } catch {}
    }

    return result;
}

/** Build a human-readable title for a proposal based on its type and fields. */
function buildProposalTitle(proposal, resolvedNames) {
    const pt = proposal.proposal_type;
    const text = proposal.proposal_text || "";
    const val = proposal.val_text || "";
    const refName = resolvedNames.valUuid || "";
    const valName = resolvedNames.valText || "";

    switch (pt) {
        case "AddStatement":
            return truncate(text, 60) || "New Statement";
        case "ChangeVariable":
            return text ? `Change ${text}${val ? ` → ${val}` : ""}` : "Change Variable";
        case "AddAction":
            return val ? `New Action: ${val}` : truncate(text, 50) || "New Action";
        case "JoinAction":
            return refName ? `Join "${refName}"` : "Join Action";
        case "EndAction":
            return refName ? `End Action: ${refName}` : "End Action";
        case "Membership":
            return refName ? `Add Member: ${refName}` : "New Member";
        case "ThrowOut":
            return refName ? `Throw Out: ${refName}` : "Throw Out Member";
        case "RemoveStatement":
            return "Remove Statement";
        case "ReplaceStatement":
            return val ? `Replace Statement → ${truncate(val, 40)}` : "Replace Statement";
        case "CreateArtifact":
            return val || text || "Create Artifact";
        case "EditArtifact":
            return `Edit: ${refName || val || "Artifact"}`;
        case "RemoveArtifact":
            return `Remove: ${refName || "Artifact"}`;
        case "DelegateArtifact":
            return `Delegate "${refName || "Artifact"}" → ${valName || "Action"}`;
        case "CommitArtifact":
            return `Commit: ${refName || "Container"}`;
        default:
            return `${pt} Proposal`;
    }
}

function ProposalDetail({ id, openDetail, agentsByUserId, communityId, bbUserId }) {
    const [proposal, setProposal] = useState(null);
    const [comments, setComments] = useState([]);
    const [supporters, setSupporters] = useState([]);
    const [resolvedNames, setResolvedNames] = useState({});
    const [loading, setLoading] = useState(true);
    // EditArtifact diff state
    const [oldArtifact, setOldArtifact] = useState(null);
    const [diffReviewed, setDiffReviewed] = useState(false);
    const [diffExpanded, setDiffExpanded] = useState(false);
    // Support action state
    const [supportBusy, setSupportBusy] = useState(false);

    const reload = () => {
        setLoading(true);
        setResolvedNames({});
        setOldArtifact(null);
        setDiffReviewed(false);
        setDiffExpanded(false);
        Promise.all([
            API.getCached(`/proposals/${id}`),
            API.getCached(`/entities/proposal/${id}/comments`).catch(() => []),
            API.getCached(`/proposals/${id}/supporters`).catch(() => []),
        ]).then(([p, c, s]) => {
            setProposal(p);
            setComments(c);
            setSupporters(s || []);
            resolveProposalRefs(p, agentsByUserId).then(setResolvedNames);
            // For EditArtifact, fetch the current (old) artifact content
            if (p.proposal_type === "EditArtifact" && p.val_uuid) {
                API.getCached(`/artifacts/${p.val_uuid}/history`).then(history => {
                    if (history?.length) setOldArtifact(history[history.length - 1]);
                }).catch(() => {});
            }
        }).finally(() => setLoading(false));
    };

    useEffect(() => { reload(); }, [id]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading proposal...</div>;
    if (!proposal) return <div className="empty-state">Proposal not found</div>;

    const creatorAgent = agentsByUserId?.[proposal.user_id];
    const creatorName = creatorAgent?.name || proposal.user_id?.slice(0, 8);
    const statusClass = proposal.proposal_status === "Accepted" ? "status-accepted" :
                        proposal.proposal_status === "Rejected" ? "status-rejected" :
                        proposal.proposal_status === "OnTheAir" ? "status-ontheair" : "status-outthere";

    const title = buildProposalTitle(proposal, resolvedNames);

    const isEdit = proposal.proposal_type === "EditArtifact";
    const isActive = proposal.proposal_status === "OutThere" || proposal.proposal_status === "OnTheAir";
    const alreadySupports = bbUserId && supporters.some(s => s.user_id === bbUserId);
    // For EditArtifact: must review diff before supporting. For others: always allowed.
    const canSupport = bbUserId && isActive && !alreadySupports && (isEdit ? diffReviewed : true);
    const canUnsupport = bbUserId && isActive && alreadySupports;

    const handleSupport = async () => {
        setSupportBusy(true);
        try {
            await API.post(`/proposals/${proposal.id}/support`, { user_id: bbUserId });
            // Refresh supporters
            const s = await API.getCached(`/proposals/${proposal.id}/supporters`).catch(() => []);
            setSupporters(s || []);
            setProposal(prev => ({ ...prev, support_count: (prev.support_count || 0) + 1 }));
        } catch (e) { alert("Support failed: " + e); }
        finally { setSupportBusy(false); }
    };

    const handleUnsupport = async () => {
        setSupportBusy(true);
        try {
            await API.delete(`/proposals/${proposal.id}/support/${bbUserId}`);
            const s = await API.getCached(`/proposals/${proposal.id}/supporters`).catch(() => []);
            setSupporters(s || []);
            setProposal(prev => ({ ...prev, support_count: Math.max(0, (prev.support_count || 0) - 1) }));
        } catch (e) { alert("Unsupport failed: " + e); }
        finally { setSupportBusy(false); }
    };

    return (
        <div className="detail-view">
            <div className="detail-section">
                <div className="detail-row">
                    <span className={`detail-badge ${statusClass}`}>{proposal.proposal_status}</span>
                    <span className="detail-type-badge">{proposal.proposal_type}</span>
                </div>
                <h2 className="detail-title">{title}</h2>
                <div className="detail-meta">
                    Created by <EntityLink type="user" id={proposal.user_id} label={creatorName} openDetail={openDetail} />
                    {" "}&middot; Age: {proposal.age} &middot; Support: {proposal.support_count}
                    {proposal.pulse_id && (
                        <span> &middot; Pulse: <EntityLink type="pulse" id={proposal.pulse_id} label="View Pulse" openDetail={openDetail} /></span>
                    )}
                </div>
            </div>

            {/* ── EditArtifact Diff View ─────────────────── */}
            {isEdit && oldArtifact && (
                <div className="detail-section" style={{ border: "1px solid rgba(78,204,163,0.3)", borderRadius: 8, padding: "0.8rem" }}>
                    <div className="detail-section-title"
                         style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}
                         onClick={() => { setDiffExpanded(e => !e); setDiffReviewed(true); }}>
                        <span>📝 Review Changes {diffReviewed ? "✓" : "(click to review)"}</span>
                        <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>{diffExpanded ? "▲ collapse" : "▼ expand"}</span>
                    </div>
                    {diffExpanded && (
                        <div style={{ marginTop: "0.6rem" }}>
                            {/* Title change */}
                            {proposal.val_text && proposal.val_text !== oldArtifact.title && (
                                <div style={{ marginBottom: "0.8rem" }}>
                                    <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--gold)", marginBottom: 4 }}>Title</div>
                                    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: "0.82rem" }}>
                                        <span style={{ background: "rgba(233,69,96,0.15)", padding: "2px 8px", borderRadius: 4, textDecoration: "line-through", color: "#e94560" }}>
                                            {oldArtifact.title}
                                        </span>
                                        <span>→</span>
                                        <span style={{ background: "rgba(78,204,163,0.15)", padding: "2px 8px", borderRadius: 4, color: "#4ecca3" }}>
                                            {proposal.val_text}
                                        </span>
                                    </div>
                                </div>
                            )}
                            {/* Content comparison */}
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                                <div>
                                    <div style={{ fontSize: "0.7rem", fontWeight: 600, color: "#e94560", marginBottom: 4 }}>Current Version</div>
                                    <pre style={{
                                        background: "rgba(233,69,96,0.08)", border: "1px solid rgba(233,69,96,0.2)",
                                        borderRadius: 6, padding: "0.5rem", fontSize: "0.72rem", whiteSpace: "pre-wrap",
                                        wordBreak: "break-word", maxHeight: 300, overflow: "auto", color: "#ccc", margin: 0
                                    }}>{oldArtifact.content || "(empty)"}</pre>
                                </div>
                                <div>
                                    <div style={{ fontSize: "0.7rem", fontWeight: 600, color: "#4ecca3", marginBottom: 4 }}>Proposed Version</div>
                                    <pre style={{
                                        background: "rgba(78,204,163,0.08)", border: "1px solid rgba(78,204,163,0.2)",
                                        borderRadius: 6, padding: "0.5rem", fontSize: "0.72rem", whiteSpace: "pre-wrap",
                                        wordBreak: "break-word", maxHeight: 300, overflow: "auto", color: "#ccc", margin: 0
                                    }}>{proposal.proposal_text || "(empty)"}</pre>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Pitch / Description — for non-EditArtifact proposals show as-is */}
            {!isEdit && proposal.proposal_text && (
                <div className="detail-section proposal-pitch-section">
                    <div className="detail-section-title">Proposal Description</div>
                    <div className="proposal-pitch-text">{proposal.proposal_text}</div>
                </div>
            )}
            {proposal.proposal_type === "ChangeVariable" && proposal.proposal_text && (
                <div className="detail-section">
                    <div className="detail-section-title">Variable Change</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                        <EntityLink
                            type="variable"
                            id={`${communityId}|${proposal.proposal_text}`}
                            label={proposal.proposal_text}
                            openDetail={openDetail}
                        />
                        {proposal.val_text && (
                            <span style={{ color: "var(--text-muted)" }}>
                                → <span className="var-inline-value">{proposal.val_text}</span>
                            </span>
                        )}
                    </div>
                </div>
            )}
            {proposal.proposal_type !== "ChangeVariable" && !isEdit && proposal.val_text && (
                <div className="detail-section">
                    <div className="detail-section-title">
                        {proposal.proposal_type === "DelegateArtifact" ? "Target Action" :
                         proposal.proposal_type === "CreateArtifact" ? "Artifact Title" :
                         proposal.proposal_type === "CommitArtifact" ? "Commit Order" :
                         "Short Name"}
                    </div>
                    <div className="detail-text-block">
                        {resolvedNames.valText || proposal.val_text}
                        {resolvedNames.valText && resolvedNames.valText !== proposal.val_text && (
                            <span className="mono" style={{ fontSize: "0.7rem", color: "var(--text-muted)", marginLeft: 8 }}>
                                ({proposal.val_text.slice(0, 12)})
                            </span>
                        )}
                    </div>
                </div>
            )}
            {proposal.val_uuid && (
                <div className="detail-section">
                    <div className="detail-section-title">
                        {["CreateArtifact"].includes(proposal.proposal_type) ? "Container" :
                         ["EditArtifact","RemoveArtifact","DelegateArtifact"].includes(proposal.proposal_type) ? "Artifact" :
                         ["CommitArtifact"].includes(proposal.proposal_type) ? "Container" :
                         ["JoinAction","EndAction"].includes(proposal.proposal_type) ? "Action" :
                         ["Membership","ThrowOut"].includes(proposal.proposal_type) ? "Member" :
                         "Referenced Entity"}
                    </div>
                    <div>
                        {resolvedNames.valUuid && (
                            <span style={{ fontWeight: 600, marginRight: 8 }}>{resolvedNames.valUuid}</span>
                        )}
                        <EntityLink
                            type={["Membership","ThrowOut"].includes(proposal.proposal_type) ? "user" : "entity"}
                            id={proposal.val_uuid}
                            label={resolvedNames.valUuid ? proposal.val_uuid.slice(0, 12) : proposal.val_uuid.slice(0, 12)}
                            openDetail={["Membership","ThrowOut"].includes(proposal.proposal_type) ? openDetail : () => {}}
                        />
                    </div>
                </div>
            )}

            {/* ── BB Support / Unsupport Button ─────────── */}
            {bbUserId && isActive && (
                <div className="detail-section" style={{ textAlign: "center" }}>
                    {canSupport && (
                        <button onClick={handleSupport} disabled={supportBusy}
                            style={{ background: "#4ecca3", color: "#111", border: "none", padding: "0.5rem 1.5rem",
                                     borderRadius: 6, fontWeight: 700, fontSize: "0.85rem", cursor: "pointer", marginRight: 8 }}>
                            {supportBusy ? "…" : "👍 Support"}
                        </button>
                    )}
                    {canUnsupport && (
                        <button onClick={handleUnsupport} disabled={supportBusy}
                            style={{ background: "#e94560", color: "#fff", border: "none", padding: "0.5rem 1.5rem",
                                     borderRadius: 6, fontWeight: 700, fontSize: "0.85rem", cursor: "pointer" }}>
                            {supportBusy ? "…" : "👎 Unsupport"}
                        </button>
                    )}
                    {isEdit && !diffReviewed && !alreadySupports && (
                        <div style={{ fontSize: "0.75rem", color: "var(--gold)", marginTop: 6 }}>
                            ⚠️ Review the changes above before you can support this proposal
                        </div>
                    )}
                </div>
            )}

            {/* Full data dump — ID, community, pulse */}
            <div className="detail-section">
                <div className="detail-section-title">Details</div>
                <div className="proposal-details-grid">
                    <span className="proposal-detail-label">ID</span>
                    <span className="proposal-detail-value mono">{proposal.id}</span>
                    <span className="proposal-detail-label">Community</span>
                    <span className="proposal-detail-value mono">
                        <EntityLink type="community" id={proposal.community_id} label={proposal.community_id} openDetail={openDetail} />
                    </span>
                    {proposal.pulse_id && <>
                        <span className="proposal-detail-label">Pulse</span>
                        <span className="proposal-detail-value mono">
                            <EntityLink type="pulse" id={proposal.pulse_id} label={proposal.pulse_id} openDetail={openDetail} />
                        </span>
                    </>}
                    <span className="proposal-detail-label">Created</span>
                    <span className="proposal-detail-value">{proposal.created_at ? new Date(proposal.created_at).toLocaleString() : "—"}</span>
                </div>
            </div>
            {supporters.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Supporters ({supporters.length})</div>
                    <div className="supporter-chips">
                        {supporters.map(s => {
                            const agent = agentsByUserId?.[s.user_id];
                            const name = agent?.name || s.user_id?.slice(0, 8);
                            return (
                                <span key={s.user_id}
                                      className={`supporter-chip ${agent ? agentColor(agent.name) : ""} clickable`}
                                      onClick={() => openDetail("user", s.user_id, name)}>
                                    {name}
                                </span>
                            );
                        })}
                    </div>
                </div>
            )}
            <div className="detail-section">
                <div className="detail-section-title">Discussion ({comments.length})</div>
                <CommentThread comments={comments} openDetail={openDetail} agentsByUserId={agentsByUserId} />
            </div>
        </div>
    );
}

// ── CommentThread (recursive tree — HN-style) ────────────
function CommentThread({ comments, openDetail, agentsByUserId }) {
    if (!comments || comments.length === 0) return <div className="empty-state" style={{ padding: 16 }}>No comments yet</div>;

    // Build tree from flat list
    const byId = {};
    const roots = [];
    comments.forEach(c => { byId[c.id] = { ...c, children: [] }; });
    comments.forEach(c => {
        if (c.parent_comment_id && byId[c.parent_comment_id]) {
            byId[c.parent_comment_id].children.push(byId[c.id]);
        } else {
            roots.push(byId[c.id]);
        }
    });
    // Sort roots newest-first; keep children ascending so threads read top-down
    const sortByTimeAsc = (a, b) => new Date(a.created_at) - new Date(b.created_at);
    const sortByTimeDesc = (a, b) => new Date(b.created_at) - new Date(a.created_at);
    roots.sort(sortByTimeDesc);
    Object.values(byId).forEach(c => c.children.sort(sortByTimeAsc));

    function CommentNode({ comment, depth }) {
        const authorAgent = agentsByUserId?.[comment.user_id];
        const authorName = authorAgent?.name || comment.user_id?.slice(0, 8);
        const colorClass = authorAgent ? agentColor(authorAgent.name) : "";
        return (
            <div className={`hn-comment ${depth > 0 ? "hn-comment-nested" : ""}`}>
                <div className="hn-comment-indent" style={{ paddingLeft: depth * 24 }}>
                    <div className="hn-comment-bar" style={{ borderLeftColor: depth === 0 ? "var(--accent)" : "var(--border)" }}></div>
                    <div className="hn-comment-body">
                        <div className="hn-comment-meta">
                            <span className={`hn-comment-author ${colorClass} entity-link`}
                                  onClick={() => openDetail("user", comment.user_id, authorName)}>
                                {authorName}
                            </span>
                            <span className="hn-comment-time">{formatTime(comment.created_at)}</span>
                            {(comment.score || 0) !== 0 && (
                                <span className="hn-comment-score">{comment.score > 0 ? "+" : ""}{comment.score} pts</span>
                            )}
                        </div>
                        <div className="hn-comment-text">{comment.text || comment.comment_text}</div>
                    </div>
                </div>
                {comment.children.map(child => (
                    <CommentNode key={child.id} comment={child} depth={depth + 1} />
                ))}
            </div>
        );
    }

    return (
        <div className="hn-comment-thread">
            {roots.map(c => <CommentNode key={c.id} comment={c} depth={0} />)}
        </div>
    );
}

// ── MemberDetail ───────────────────────────────────────
function MemberDetail({ id, openDetail, agents, agentsByUserId, communityId, events }) {
    const [user, setUser] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [membershipProposals, setMembershipProposals] = useState([]);
    const [communities, setCommunities] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        const fetches = [
            API.getCached(`/users/${id}`),
            communityId
                ? API.getCached(`/communities/${communityId}/proposals?user_id=${id}`).catch(() => [])
                : Promise.resolve([]),
            communityId
                ? API.getCached(`/communities/${communityId}/proposals?val_uuid=${id}&proposal_type=Membership`).catch(() => [])
                : Promise.resolve([]),
            API.getCached(`/users/${id}/communities`).catch(() => []),
        ];
        Promise.all(fetches).then(([u, p, mp, comms]) => {
            setUser(u);
            setProposals(p);
            setMembershipProposals(mp || []);
            // Fetch community names for each membership
            const communityPromises = (comms || []).map(m =>
                API.getCached(`/communities/${m.community_id}`).then(c => ({
                    ...m,
                    community_name: c.name || c.community_name || m.community_id.slice(0, 8),
                })).catch(() => ({
                    ...m,
                    community_name: m.community_id.slice(0, 8),
                }))
            );
            return Promise.all(communityPromises);
        }).then(commsWithNames => {
            setCommunities(commsWithNames || []);
        }).finally(() => setLoading(false));
    }, [id, communityId]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading member...</div>;
    if (!user) return <div className="empty-state">User not found</div>;

    // Find agent info for this user
    const agent = (agents || []).find(a => a.user_id === id);
    // Filter events by agent name
    const agentEvents = agent ? (events || []).filter(e => e.agent === agent.name) : [];

    return (
        <div className="detail-view">
            <div className="detail-section">
                <h2 className="detail-title">{user.user_name}</h2>
                {user.about && <div className="detail-text-block">{user.about}</div>}
                <div className="detail-meta">
                    Joined: {formatDate(user.created_at)}
                </div>
            </div>
            {agent && (
                <div className="detail-section">
                    <div className="detail-section-title">Agent Profile</div>
                    <div className="member-agent-info">
                        <div>
                            <div className="detail-meta">Role: <strong>{agent.role}</strong></div>
                            <div className="detail-text-block" style={{ marginTop: 8 }}>{agent.background}</div>
                        </div>
                        {agent.traits && <TraitsRadarChart traits={agent.traits} agentName={agent.name} />}
                    </div>
                </div>
            )}
            {communities.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Communities ({communities.length})</div>
                    <div className="detail-list">
                        {communities.map(c => (
                            <div key={c.community_id} className="detail-list-item clickable" onClick={() => openDetail("community", c.community_id, c.community_name)}>
                                <span className={`mini-badge ${c.status === 1 ? "status-accepted" : ""}`}>
                                    {c.status === 1 ? "Active" : c.status === 0 ? "Pending" : "Inactive"}
                                </span>
                                <span className="detail-list-text">{c.community_name}</span>
                                <span className="detail-list-meta">Seniority: {c.seniority || 0}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
            {membershipProposals.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Membership Proposal</div>
                    <div className="detail-list">
                        {membershipProposals.map(p => {
                            const proposer = agentsByUserId?.[p.user_id];
                            const proposerName = proposer?.name || p.user_id?.slice(0, 8);
                            return (
                                <div key={p.id} className="detail-list-item clickable" onClick={() => openDetail("proposal", p.id, "Membership")}>
                                    <span className={`mini-badge ${p.proposal_status === "Accepted" ? "status-accepted" : p.proposal_status === "Rejected" ? "status-rejected" : ""}`}>
                                        {p.proposal_status}
                                    </span>
                                    <span className="detail-list-text">Proposed by {proposerName}</span>
                                    <span className="detail-list-meta">Support: {p.support_count}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
            <div className="detail-section">
                <div className="detail-section-title">Proposals ({proposals.length})</div>
                {proposals.length === 0 && <div className="empty-state" style={{ padding: 16 }}>No proposals</div>}
                <div className="detail-list">
                    {proposals.map(p => (
                        <div key={p.id} className="detail-list-item clickable" onClick={() => openDetail("proposal", p.id, `${p.proposal_type}`)}>
                            <span className={`mini-badge ${p.proposal_status === "Accepted" ? "status-accepted" : p.proposal_status === "Rejected" ? "status-rejected" : ""}`}>
                                {p.proposal_status}
                            </span>
                            <span className="detail-type-badge">{p.proposal_type}</span>
                            <span className="detail-list-text">{truncate(proposalDisplayText(p), 60)}</span>
                        </div>
                    ))}
                </div>
            </div>
            {agentEvents.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Recent Activity ({agentEvents.length})</div>
                    <div className="detail-list" style={{ maxHeight: 300, overflowY: "auto" }}>
                        {[...agentEvents].reverse().slice(0, 50).map((ev, i) => (
                            <div key={i} className="detail-list-item clickable">
                                <span className="comment-time">{formatTime(ev.time)}</span>
                                <span className={`event-badge badge-${ev.action}`}>{ev.action}</span>
                                <span className="detail-list-text">
                                    <LinkedDetails details={ev.details} refId={ev.ref_id} openDetail={openDetail} />
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

// ── CommunityDetail + VariablesPanel ───────────────────
function CommunityDetail({ id, openDetail, agentsByUserId }) {
    const [community, setCommunity] = useState(null);
    const [variables, setVariables] = useState([]);
    const [members, setMembers] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        Promise.all([
            API.getCached(`/communities/${id}`),
            API.getCached(`/communities/${id}/variables`).catch(() => []),
            API.getCached(`/communities/${id}/members`).catch(() => []),
        ]).then(([c, v, m]) => {
            setCommunity(c);
            setVariables(parseVariables(v));
            setMembers(m);
        }).finally(() => setLoading(false));
    }, [id]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading community...</div>;
    if (!community) return <div className="empty-state">Community not found</div>;

    // Group variables by category
    const varGroups = {};
    (variables || []).forEach(v => {
        const cat = categorizeVariable(v.name);
        if (!varGroups[cat]) varGroups[cat] = [];
        varGroups[cat].push(v);
    });

    return (
        <div className="detail-view">
            <div className="detail-section">
                <h2 className="detail-title">{community.name}</h2>
                <div className="detail-meta">
                    Members: {community.member_count || 0}
                    {community.parent_id && (
                        <span> &middot; Parent: <EntityLink type="community" id={community.parent_id} label="Parent Community" openDetail={openDetail} /></span>
                    )}
                </div>
            </div>
            <div className="detail-section">
                <div className="detail-section-title">Variables</div>
                {Object.keys(varGroups).length === 0 && <div className="empty-state" style={{ padding: 16 }}>No variables</div>}
                {Object.entries(varGroups).map(([cat, vars]) => (
                    <div key={cat} className="var-group">
                        <div className="var-group-title">{cat}</div>
                        <div className="var-grid">
                            {vars.map(v => (
                                <div key={v.name} className="var-item clickable"
                                     onClick={() => openDetail("variable", `${id}|${v.name}`, v.name)}>
                                    <span className="var-name">{v.name}</span>
                                    <span className="var-value">{v.value}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
            <div className="detail-section">
                <div className="detail-section-title">Members ({members.length})</div>
                <div className="member-grid">
                    {members.map(m => {
                        const agent = agentsByUserId?.[m.user_id];
                        const name = agent?.name || m.user_id?.slice(0, 8);
                        return (
                            <div key={m.user_id} className="member-chip clickable" onClick={() => openDetail("user", m.user_id, name)}>
                                <span className={`member-name ${agent ? agentColor(agent.name) : ""}`}>{name}</span>
                                <span className="member-seniority">Sen: {m.seniority || 0}</span>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

function categorizeVariable(name) {
    const n = name.toLowerCase();
    if (n.includes("artifact")) return "Artifacts";
    if (n.includes("threshold") || n.includes("quorum") || n.includes("majority")) return "Thresholds";
    if (n.includes("age") || n.includes("cooldown") || n.includes("pulse")) return "Governance";
    if (n.includes("member") || n.includes("handler")) return "Membership";
    return "General";
}

// ── PulseDetail ────────────────────────────────────────
function PulseDetail({ id, openDetail, agentsByUserId, communityId }) {
    const [pulse, setPulse] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [supporters, setSupporters] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        const fetches = [
            API.getCached(`/pulses/${id}`),
            API.getCached(`/pulses/${id}/supporters`).catch(() => []),
        ];
        if (communityId) {
            fetches.push(API.getCached(`/communities/${communityId}/proposals`).catch(() => []));
        } else {
            fetches.push(Promise.resolve([]));
        }
        Promise.all(fetches).then(([p, s, allProps]) => {
            setPulse(p);
            setSupporters(s || []);
            setProposals((allProps || []).filter(pr => pr.pulse_id === id));
        }).finally(() => setLoading(false));
    }, [id, communityId]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading pulse...</div>;
    if (!pulse) return <div className="empty-state">Pulse not found</div>;

    const statusLabel = pulse.status === 0 ? "Next" : pulse.status === 1 ? "Active" : "Done";

    return (
        <div className="detail-view">
            <div className="detail-section">
                <h2 className="detail-title">Pulse - {statusLabel}</h2>
                <div className="detail-meta">
                    Support: {pulse.support_count}/{pulse.threshold} &middot; Created: {formatDate(pulse.created_at)}
                </div>
            </div>
            {supporters.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Pulse Supporters ({supporters.length})</div>
                    <div className="supporter-chips">
                        {supporters.map(s => {
                            const agent = agentsByUserId?.[s.user_id];
                            const name = agent?.name || s.user_id?.slice(0, 8);
                            return (
                                <span key={s.user_id}
                                      className={`supporter-chip ${agent ? agentColor(agent.name) : ""} clickable`}
                                      onClick={() => openDetail("user", s.user_id, name)}>
                                    {name}
                                </span>
                            );
                        })}
                    </div>
                </div>
            )}
            <div className="detail-section">
                <div className="detail-section-title">Proposals in this Pulse ({proposals.length})</div>
                {proposals.length === 0 && <div className="empty-state" style={{ padding: 16 }}>No proposals</div>}
                <div className="detail-list">
                    {proposals.map(p => {
                        const creator = agentsByUserId?.[p.user_id];
                        return (
                            <div key={p.id} className="detail-list-item clickable" onClick={() => openDetail("proposal", p.id, `${p.proposal_type}`)}>
                                <span className={`mini-badge ${p.proposal_status === "Accepted" ? "status-accepted" : p.proposal_status === "Rejected" ? "status-rejected" : ""}`}>
                                    {p.proposal_status}
                                </span>
                                <span className="detail-type-badge">{p.proposal_type}</span>
                                <span className="detail-list-text">{truncate(proposalDisplayText(p), 50)}</span>
                                {creator && <span className="detail-list-meta">by {creator.name}</span>}
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

// ── StatementDetail ────────────────────────────────────
function StatementDetail({ id, openDetail, communityId, agentsByUserId }) {
    const [statement, setStatement] = useState(null);
    const [relatedProposals, setRelatedProposals] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        API.getCached(`/statements/${id}`).then(s => {
            setStatement(s);
            // Find proposals that created/replaced this statement
            if (s && s.community_id) {
                API.getCached(`/communities/${s.community_id}/proposals`).then(proposals => {
                    const stmtText = s.statement_text || s.text || "";
                    const related = (proposals || []).filter(p =>
                        (p.proposal_type === "AddStatement" || p.proposal_type === "ReplaceStatement") &&
                        (p.proposal_text === stmtText || p.val_text === stmtText)
                    );
                    related.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
                    setRelatedProposals(related);
                }).catch(() => setRelatedProposals([]));
            }
        }).finally(() => setLoading(false));
    }, [id]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading statement...</div>;
    if (!statement) return <div className="empty-state">Statement not found</div>;

    return (
        <div className="detail-view">
            <div className="detail-section">
                <h2 className="detail-title">Statement</h2>
                <div className="detail-meta">
                    Status: {statement.status === 1 ? "Active" : "Removed"} &middot; Created: {formatDate(statement.created_at)}
                </div>
            </div>
            <div className="detail-section">
                <div className="detail-section-title">Text</div>
                <div className="detail-text-block">{statement.statement_text || statement.text}</div>
            </div>
            {relatedProposals.length > 0 && (
                <div className="detail-section">
                    <div className="detail-section-title">Related Proposals ({relatedProposals.length})</div>
                    <div className="detail-list">
                        {relatedProposals.map(p => {
                            const creator = agentsByUserId?.[p.user_id];
                            return (
                                <div key={p.id} className="detail-list-item clickable" onClick={() => openDetail("proposal", p.id, p.proposal_type)}>
                                    <span className={`mini-badge ${p.proposal_status === "Accepted" ? "status-accepted" : p.proposal_status === "Rejected" ? "status-rejected" : ""}`}>
                                        {p.proposal_status}
                                    </span>
                                    <span className="detail-type-badge">{p.proposal_type}</span>
                                    {creator && <span className="detail-list-meta">by {creator.name}</span>}
                                    <span className="detail-list-meta" style={{ marginLeft: "auto" }}>{formatDate(p.created_at)}</span>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}
            {statement.prev_statement_id && (
                <div className="detail-section">
                    <div className="detail-section-title">Replaces</div>
                    <EntityLink type="statement" id={statement.prev_statement_id} label="Previous Statement" openDetail={openDetail} />
                </div>
            )}
        </div>
    );
}

// ── Statements Tab ─────────────────────────────────────
function StatementsTab({ communityId, openDetail }) {
    const [statements, setStatements] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!communityId) return;
        setLoading(true);
        API.getCached(`/communities/${communityId}/statements`).then(s => setStatements(s)).catch(() => setStatements([])).finally(() => setLoading(false));
    }, [communityId]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading statements...</div>;

    const active = statements.filter(s => s.status === 1);
    const removed = statements.filter(s => s.status !== 1);

    return (
        <div className="card">
            <div className="card-title">Community Constitution ({active.length} active statements)</div>
            {active.length === 0 && <div className="empty-state">No active statements yet</div>}
            <div className="statements-list">
                {active.map(s => (
                    <div key={s.id} className="statement-item clickable" onClick={() => openDetail("statement", s.id, truncate(s.statement_text || s.text, 30))}>
                        <div className="statement-text">{s.statement_text || s.text}</div>
                        <div className="statement-meta">
                            {formatDate(s.created_at)}
                            {s.prev_statement_id && <span> &middot; Replaces previous</span>}
                        </div>
                    </div>
                ))}
            </div>
            {removed.length > 0 && (
                <div style={{ marginTop: 16 }}>
                    <div className="card-title" style={{ color: "var(--text-muted)" }}>Removed ({removed.length})</div>
                    {removed.map(s => (
                        <div key={s.id} className="statement-item removed clickable" onClick={() => openDetail("statement", s.id, truncate(s.statement_text || s.text, 30))}>
                            <div className="statement-text">{s.statement_text || s.text}</div>
                            <div className="statement-meta">{formatDate(s.created_at)}</div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Action Tree Tab ────────────────────────────────────
function ActionTreeTab({ communityId, rootCommunityId, openDetail, onNavigate }) {
    const [actions, setActions] = useState([]);
    const [loading, setLoading] = useState(true);

    const rootId = rootCommunityId || communityId;

    useEffect(() => {
        if (!rootId) return;
        setLoading(true);
        API.getCached(`/communities/${rootId}/actions`).then(a => setActions(a)).catch(() => setActions([])).finally(() => setLoading(false));
    }, [rootId]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading actions...</div>;

    return (
        <div className="card">
            <div className="card-title">Action Tree</div>
            <div className="action-tree">
                <div className="tree-root clickable" onClick={() => onNavigate(null, "Root Community")}>
                    🏠 Root Community
                </div>
                {actions.length === 0 && <div className="empty-state">No actions spawned yet</div>}
                {actions.map(a => (
                    <ActionTreeNode key={a.action_id} action={a} openDetail={openDetail} onNavigate={onNavigate} depth={1} />
                ))}
            </div>
        </div>
    );
}

// ── Compact Sidebar Action Tree (always visible alongside main content) ────
function ActionSidebar({ rootCommunityId, activeCommunityId, onNavigate, openDetail, round, isOpen, onClose }) {
    const [actions, setActions] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!rootCommunityId) return;
        setLoading(true);
        API.get(`/communities/${rootCommunityId}/actions`)
            .then(a => setActions(a))
            .catch(() => setActions([]))
            .finally(() => setLoading(false));
    }, [rootCommunityId, round]);

    // Don't show sidebar if no actions exist (desktop only — on mobile it's hidden by default)
    if (!loading && actions.length === 0) return null;

    function handleNav(id, name) {
        onNavigate(id, name);
        if (onClose) onClose();  // auto-close on mobile
    }

    return (
        <React.Fragment>
            {isOpen && <div className="sidebar-overlay" onClick={onClose} />}
            <div className={`action-sidebar ${isOpen ? 'open' : ''}`}>
                <div className="action-sidebar-title">Communities</div>
                <div
                    className={`action-sidebar-item ${!activeCommunityId ? "active" : ""}`}
                    onClick={() => handleNav(null, null)}
                >
                    🏠 Root
                </div>
                {loading && <div style={{ padding: "8px 12px", color: "var(--text-muted)", fontSize: "0.75rem" }}>Loading...</div>}
                {actions.map(a => {
                    const isActive = activeCommunityId === a.action_id;
                    return (
                        <div
                            key={a.action_id}
                            className={`action-sidebar-item ${isActive ? "active" : ""}`}
                            onClick={() => handleNav(a.action_id, a.name || a.action_id.slice(0, 8))}
                            title={a.name || a.action_id}
                        >
                            <span className="action-sidebar-icon">⚡</span>
                            <span className="action-sidebar-name">{a.name || a.action_id.slice(0, 8)}</span>
                        </div>
                    );
                })}
            </div>
        </React.Fragment>
    );
}

// ── Action Breadcrumb ───────────────────────────────
const ZERO_UUID = "00000000-0000-0000-0000-000000000000";
const _communityCache = {};

function ActionBreadcrumb({ activeCommunityId, rootCommunityId, rootCommunityName, onNavigate }) {
    const [chain, setChain] = useState([]);

    useEffect(() => {
        if (!activeCommunityId || !rootCommunityId) { setChain([]); return; }

        async function buildChain() {
            const path = [];
            let currentId = activeCommunityId;

            // Walk up parent_id until we reach root or zero UUID
            while (currentId && currentId !== rootCommunityId && currentId !== ZERO_UUID) {
                let comm = _communityCache[currentId];
                if (!comm) {
                    try {
                        comm = await API.get(`/communities/${currentId}`);
                        _communityCache[currentId] = comm;
                    } catch { break; }
                }
                path.unshift({ id: comm.id, name: comm.name || comm.id.slice(0, 8) });
                currentId = comm.parent_id;
            }

            setChain(path);
        }
        buildChain();
    }, [activeCommunityId, rootCommunityId]);

    if (chain.length === 0) return null;

    return (
        <div className="action-breadcrumb">
            <span
                className="action-breadcrumb-item"
                onClick={() => onNavigate(null, null)}
            >
                {rootCommunityName || 'Root'}
            </span>
            {chain.map((item, i) => {
                const isCurrent = i === chain.length - 1;
                return (
                    <React.Fragment key={item.id}>
                        <span className="action-breadcrumb-sep">&rsaquo;</span>
                        <span
                            className={`action-breadcrumb-item ${isCurrent ? 'current' : ''}`}
                            onClick={isCurrent ? undefined : () => onNavigate(item.id, item.name)}
                        >
                            {item.name}
                        </span>
                    </React.Fragment>
                );
            })}
        </div>
    );
}

function ActionTreeNode({ action, openDetail, onNavigate, depth }) {
    const [expanded, setExpanded] = useState(false);
    const [children, setChildren] = useState([]);
    const [childCommunity, setChildCommunity] = useState(null);
    const [loaded, setLoaded] = useState(false);

    function handleExpand(e) {
        e.stopPropagation();
        if (!loaded) {
            setLoaded(true);
            Promise.all([
                API.getCached(`/communities/${action.action_id}`).catch(() => null),
                API.getCached(`/communities/${action.action_id}/actions`).catch(() => []),
            ]).then(([comm, acts]) => {
                setChildCommunity(comm);
                setChildren(acts);
            });
        }
        setExpanded(!expanded);
    }

    const statusLabel = action.status === 1 ? "Active" : "Ended";
    const name = action.name || childCommunity?.name || action.action_id.slice(0, 12);

    return (
        <div className="tree-node" style={{ marginLeft: depth * 24 }}>
            <div className="tree-node-header">
                <span className="tree-expand" onClick={handleExpand}>{expanded ? "▾" : "▸"}</span>
                <span className="tree-node-name clickable" onClick={() => onNavigate(action.action_id, name)}>
                    {name}
                </span>
                <span className={`mini-badge ${statusLabel === "Active" ? "status-accepted" : "status-rejected"}`}>{statusLabel}</span>
                {childCommunity && <span className="tree-node-info" style={{ marginLeft: 8 }}>({childCommunity.member_count || 0} members)</span>}
            </div>
            {expanded && (
                <div className="tree-children">
                    {children.map(c => (
                        <ActionTreeNode key={c.action_id} action={c} openDetail={openDetail} onNavigate={onNavigate} depth={depth + 1} />
                    ))}
                    {loaded && children.length === 0 && <div className="tree-node-info">No sub-actions</div>}
                </div>
            )}
        </div>
    );
}

// ── Variable Detail ─────────────────────────────────────
function VariableDetail({ id, openDetail, agentsByUserId }) {
    // id is "communityId|varName"
    const parts = id.split("|");
    const varCommunityId = parts[0];
    const varName = parts.slice(1).join("|");

    const [currentValue, setCurrentValue] = useState(null);
    const [history, setHistory] = useState([]);
    const [communityName, setCommunityName] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setLoading(true);
        Promise.all([
            API.getCached(`/communities/${varCommunityId}/variables`).catch(() => []),
            API.getCached(`/communities/${varCommunityId}/proposals`).catch(() => []),
            API.getCached(`/communities/${varCommunityId}`).catch(() => null),
        ]).then(([vars, proposals, comm]) => {
            const varObj = parseVariables(vars).find(v => v.name === varName);
            setCurrentValue(varObj?.value ?? "—");
            const hist = (proposals || []).filter(p =>
                p.proposal_type === "ChangeVariable" &&
                (p.proposal_text === varName || p.proposal_text.startsWith(varName + "\n"))
            );
            hist.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
            setHistory(hist);
            setCommunityName(comm?.name || null);
        }).finally(() => setLoading(false));
    }, [varCommunityId, varName]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading variable...</div>;

    return (
        <div className="detail-view">
            <div className="detail-section">
                <h2 className="detail-title">{varName}</h2>
                <div className="detail-meta">
                    Current Value: <span className="var-inline-value" style={{ fontSize: "1rem" }}>{currentValue}</span>
                </div>
                <div className="detail-meta">
                    Community: <EntityLink type="community" id={varCommunityId} label={communityName || varCommunityId.slice(0, 8)} openDetail={openDetail} />
                </div>
                <div className="detail-meta" style={{ marginTop: 4, color: "var(--text-muted)", fontSize: "0.75rem" }}>
                    {categorizeVariable(varName)}
                </div>
            </div>
            <div className="detail-section">
                <div className="detail-section-title">Change History ({history.length} proposals)</div>
                {history.length === 0 && (
                    <div className="empty-state" style={{ padding: 16 }}>No ChangeVariable proposals for this variable yet</div>
                )}
                <div className="detail-list">
                    {history.map(p => {
                        const creator = agentsByUserId?.[p.user_id];
                        const isAccepted = p.proposal_status === "Accepted";
                        const isRejected = p.proposal_status === "Rejected";
                        return (
                            <div key={p.id} className="detail-list-item clickable"
                                 onClick={() => openDetail("proposal", p.id, `ChangeVariable: ${varName}`)}>
                                <span className={`mini-badge ${isAccepted ? "status-accepted" : isRejected ? "status-rejected" : ""}`}>
                                    {p.proposal_status}
                                </span>
                                <span className="var-inline-value" style={{ margin: "0 8px" }}>→ {p.val_text || "?"}</span>
                                {creator && <span className="detail-list-meta">by {creator.name}</span>}
                                <span className="detail-list-meta" style={{ marginLeft: "auto" }}>{formatDate(p.created_at)}</span>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

// ── Variables Tab ─────────────────────────────────────
function VariablesTab({ communityId, openDetail }) {
    const [variables, setVariables] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!communityId) return;
        setLoading(true);
        API.getCached(`/communities/${communityId}/variables`, 8000)
            .then(v => setVariables(parseVariables(v)))
            .catch(() => setVariables([]))
            .finally(() => setLoading(false));
    }, [communityId]);

    if (!communityId) return <div className="loading-center">Waiting for community...</div>;
    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading variables...</div>;

    const varGroups = {};
    variables.forEach(v => {
        const cat = categorizeVariable(v.name);
        if (!varGroups[cat]) varGroups[cat] = [];
        varGroups[cat].push(v);
    });

    return (
        <div className="card">
            <div className="card-title">Community Variables ({variables.length})</div>
            {variables.length === 0 && <div className="empty-state">No variables loaded yet</div>}
            {Object.entries(varGroups).map(([cat, vars]) => (
                <div key={cat} className="var-group">
                    <div className="var-group-title">{cat}</div>
                    <div className="var-grid">
                        {vars.map(v => (
                            <div key={v.name} className="var-item clickable"
                                 onClick={() => openDetail("variable", `${communityId}|${v.name}`, v.name)}>
                                <span className="var-name">{v.name}</span>
                                <span className="var-value">{v.value}</span>
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}

// ── LLM Switcher ────────────────────────────────────────
const LLM_LABELS = {
    "custom":              "— custom —",
    "claude-haiku":        "⚡ Claude Haiku",
    "ollama-gemma4":       "🦙 Ollama gemma4:26b",
    "ollama-gemma4-e4b":   "🦙 Ollama gemma4:e4b",
    "ollama-qwen3":        "🤖 Ollama qwen3:8b",
    "ollama-qwen3-think":  "🧠 Ollama qwen3:8b (think)",
    "or-mistral-small":    "🌐 OR mistral-small",
    "or-lunaris":          "🌐 OR lunaris-8b",
    "or-gemini-flash-lite":"🌐 OR gemini-2.5-flash-lite",
};

function LLMSwitcher({ currentPreset }) {
    const [switching, setSwitching] = React.useState(false);
    const [current, setCurrent] = React.useState(currentPreset || "custom");

    React.useEffect(() => {
        if (currentPreset) setCurrent(currentPreset);
    }, [currentPreset]);

    async function handleChange(e) {
        const preset = e.target.value;
        setSwitching(true);
        try {
            await API.post("/simulation/llm", { preset });
            setCurrent(preset);
        } catch (err) {
            alert(`Failed to switch LLM: ${err.message}`);
        } finally {
            setSwitching(false);
        }
    }

    return (
        <div className="header-stat" title="Switch LLM backend mid-session">
            <span className="label">LLM</span>
            <select
                value={current}
                onChange={handleChange}
                disabled={switching}
                style={{
                    background: "var(--surface)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "2px 6px",
                    fontSize: "0.78rem",
                    cursor: switching ? "wait" : "pointer",
                    opacity: switching ? 0.6 : 1,
                }}
            >
                {Object.entries(LLM_LABELS).map(([key, label]) => (
                    <option key={key} value={key}>{label}</option>
                ))}
            </select>
            {switching && <span style={{ marginLeft: 4, fontSize: "0.7rem", color: "var(--text-muted)" }}>…</span>}
        </div>
    );
}

// ── Header ──────────────────────────────────────────────
function Header({ status, openDetail, activeCommunityId, activeCommunityName, onBackToRoot, onToggleSidebar }) {
    const community = status?.community;
    return (
        <div className="header">
            <div className="header-left">
                <button className="hamburger-btn" onClick={onToggleSidebar} title="Toggle sidebar">
                    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="3" y1="5" x2="17" y2="5"/><line x1="3" y1="10" x2="17" y2="10"/><line x1="3" y1="15" x2="17" y2="15"/>
                    </svg>
                </button>
                <div className="header-title">
                    KBZ BIG BROTHER
                    <span
                        className="entity-link header-community-name"
                        onClick={() => community && openDetail("community", community.id, community.name)}
                    >
                        {community?.name || "Loading..."}
                    </span>
                </div>
            </div>
            <div className="header-stats">
                <div className="header-stat">
                    <span className="label">Round</span>
                    <span className="value">{status?.round || 0}</span>
                </div>
                <div className="header-stat">
                    <span className="label">Members</span>
                    <span className="value">{community?.member_count || 0}</span>
                </div>
                <div className="header-stat header-stat-events">
                    <span className="label">Events</span>
                    <span className="value">{status?.total_events || 0}</span>
                </div>
                <LLMSwitcher currentPreset={status?.llm?.preset} />
                {status?.llm?.avg_latency_s > 0 && (
                    <div className="header-stat header-stat-latency" title={`${status.llm.calls} calls, ${status.llm.errors} errors`}>
                        <span className="label">Avg</span>
                        <span className="value" style={{ fontSize: "0.75rem" }}>{status.llm.avg_latency_s}s</span>
                    </div>
                )}
                {status?.paused && (
                    <div className="header-stat">
                        <span className="value" style={{ color: "var(--warning)" }}>PAUSED</span>
                    </div>
                )}
            </div>
        </div>
    );
}

// ── Relationships helpers ───────────────────────────────
function buildPairMap(pairs) {
    const m = {};
    for (const p of pairs) {
        const key = p.user_id1 < p.user_id2 ? `${p.user_id1}|${p.user_id2}` : `${p.user_id2}|${p.user_id1}`;
        m[key] = p.score;
    }
    return m;
}
function lookupPair(map, a, b) {
    if (a === b) return null;
    const key = a < b ? `${a}|${b}` : `${b}|${a}`;
    return map[key] ?? 0; // missing pair = 0 closeness
}

// Classical Multi-Dimensional Scaling: project a closeness matrix into 2D.
// Closer pairs (higher score) end up nearer in the plane.
function mdsLayout(members, pairMap) {
    const n = members.length;
    const ids = members.map((m) => m.user_id);
    if (n === 0) return [];
    if (n === 1) return [{ user_id: ids[0], x: 0, y: 0 }];

    // 1. Convert closeness → distance. Higher closeness = smaller distance.
    let cMax = -Infinity;
    for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
            const a = ids[i], b = ids[j];
            const k = a < b ? `${a}|${b}` : `${b}|${a}`;
            const s = pairMap[k] ?? 0;
            if (s > cMax) cMax = s;
        }
    }
    if (!isFinite(cMax)) cMax = 0;

    // 2. Build squared distance matrix D².
    const D2 = Array.from({ length: n }, () => new Array(n).fill(0));
    for (let i = 0; i < n; i++) {
        for (let j = i + 1; j < n; j++) {
            const a = ids[i], b = ids[j];
            const k = a < b ? `${a}|${b}` : `${b}|${a}`;
            const s = pairMap[k] ?? 0;
            const d = cMax - s; // ≥ 0
            D2[i][j] = D2[j][i] = d * d;
        }
    }

    // 3. Double-center: B = -1/2 * J * D² * J,  J = I - (1/n) * 1·1ᵀ
    const rowMean = D2.map((r) => r.reduce((a, b) => a + b, 0) / n);
    let grand = 0;
    for (let i = 0; i < n; i++) grand += rowMean[i];
    grand /= n;
    const B = Array.from({ length: n }, () => new Array(n).fill(0));
    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            B[i][j] = -0.5 * (D2[i][j] - rowMean[i] - rowMean[j] + grand);
        }
    }

    // 4. Top-2 eigenvectors via power iteration with deflation.
    const v1 = powerIter(B, 60);
    const lam1 = rayleigh(B, v1);
    const Bd = B.map((row, i) => row.map((val, j) => val - lam1 * v1[i] * v1[j]));
    const v2 = powerIter(Bd, 60);
    const lam2 = rayleigh(Bd, v2);

    const s1 = Math.sqrt(Math.max(0, lam1));
    const s2 = Math.sqrt(Math.max(0, lam2));

    return ids.map((uid, i) => ({ user_id: uid, x: s1 * v1[i], y: s2 * v2[i] }));
}

function powerIter(M, iters) {
    const n = M.length;
    let v = new Array(n).fill(0).map(() => Math.random() - 0.5);
    for (let it = 0; it < iters; it++) {
        const w = new Array(n).fill(0);
        for (let i = 0; i < n; i++) {
            let s = 0;
            for (let j = 0; j < n; j++) s += M[i][j] * v[j];
            w[i] = s;
        }
        let norm = 0;
        for (let i = 0; i < n; i++) norm += w[i] * w[i];
        norm = Math.sqrt(norm) || 1;
        for (let i = 0; i < n; i++) v[i] = w[i] / norm;
    }
    return v;
}

function rayleigh(M, v) {
    const n = M.length;
    let num = 0, den = 0;
    for (let i = 0; i < n; i++) {
        let s = 0;
        for (let j = 0; j < n; j++) s += M[i][j] * v[j];
        num += v[i] * s;
        den += v[i] * v[i];
    }
    return num / (den || 1);
}

// ── Relationships: Heatmap View ─────────────────────────
function HeatmapView({ data, agentsByUserId, openDetail }) {
    const { members, pairs } = data;
    const pairMap = useMemo(() => buildPairMap(pairs), [pairs]);

    // Seriation: sort members by sum of their pairwise scores (cheap proxy
    // for first principal component — clusters land near the diagonal).
    const sorted = useMemo(() => {
        const sums = {};
        for (const m of members) {
            let s = 0;
            for (const other of members) {
                if (other.user_id !== m.user_id) s += lookupPair(pairMap, m.user_id, other.user_id);
            }
            sums[m.user_id] = s;
        }
        return [...members].sort((a, b) => sums[b.user_id] - sums[a.user_id]);
    }, [members, pairMap]);

    const maxAbs = Math.max(0.001, ...pairs.map((p) => Math.abs(p.score)));
    const cellSize = Math.max(14, Math.min(36, Math.floor(560 / Math.max(1, sorted.length))));
    const labelW = 110;

    const colorFor = (score) => {
        if (score === null) return "#1a1a2e";
        const t = Math.max(-1, Math.min(1, score / maxAbs));
        if (t >= 0) return `rgba(78, 204, 163, ${(t * 0.85).toFixed(2)})`;
        return `rgba(233, 69, 96, ${(-t * 0.85).toFixed(2)})`;
    };

    const labelOf = (uid) => agentsByUserId[uid]?.name || uid.slice(0, 6);

    return (
        <div style={{ overflow: "auto", background: "#0f0f1e", borderRadius: 6, padding: 12 }}>
            <table style={{ borderCollapse: "collapse", fontSize: "0.75rem", color: "#eee" }}>
                <thead>
                    <tr>
                        <th style={{ width: labelW }}></th>
                        {sorted.map((m) => (
                            <th
                                key={m.user_id}
                                title={labelOf(m.user_id)}
                                style={{ width: cellSize, height: labelW, verticalAlign: "bottom", padding: 0 }}
                            >
                                <div
                                    style={{
                                        transform: "rotate(-60deg)",
                                        transformOrigin: "left bottom",
                                        width: cellSize,
                                        whiteSpace: "nowrap",
                                        cursor: "pointer",
                                    }}
                                    onClick={() => openDetail("user", m.user_id, labelOf(m.user_id))}
                                >
                                    {labelOf(m.user_id)}
                                </div>
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {sorted.map((row) => (
                        <tr key={row.user_id}>
                            <td
                                style={{ textAlign: "right", paddingRight: 6, cursor: "pointer", whiteSpace: "nowrap" }}
                                onClick={() => openDetail("user", row.user_id, labelOf(row.user_id))}
                            >
                                {labelOf(row.user_id)}
                            </td>
                            {sorted.map((col) => {
                                const score = row.user_id === col.user_id ? null : lookupPair(pairMap, row.user_id, col.user_id);
                                return (
                                    <td
                                        key={col.user_id}
                                        title={
                                            score === null
                                                ? ""
                                                : `${labelOf(row.user_id)} ↔ ${labelOf(col.user_id)}: ${score >= 0 ? "+" : ""}${score.toFixed(2)}`
                                        }
                                        style={{
                                            width: cellSize,
                                            height: cellSize,
                                            background: colorFor(score),
                                            border: "1px solid #0f0f1e",
                                        }}
                                    ></td>
                                );
                            })}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ── Relationships: Scatter (MDS) View ───────────────────
function ScatterView({ data, agentsByUserId, openDetail }) {
    const { members, pairs } = data;
    const pairMap = useMemo(() => buildPairMap(pairs), [pairs]);
    const coords = useMemo(() => mdsLayout(members, pairMap), [members, pairMap]);

    const W = 720, H = 520, PAD = 40;
    if (coords.length === 0) return null;
    const xs = coords.map((c) => c.x), ys = coords.map((c) => c.y);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const sx = (x) => PAD + ((x - xMin) / (xMax - xMin || 1)) * (W - 2 * PAD);
    const sy = (y) => PAD + ((y - yMin) / (yMax - yMin || 1)) * (H - 2 * PAD);

    const labelOf = (uid) => agentsByUserId[uid]?.name || uid.slice(0, 6);

    return (
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ background: "#0f0f1e", borderRadius: 6 }}>
            {coords.map((c) => {
                const cx = sx(c.x), cy = sy(c.y);
                return (
                    <g
                        key={c.user_id}
                        style={{ cursor: "pointer" }}
                        onClick={() => openDetail("user", c.user_id, labelOf(c.user_id))}
                    >
                        <circle cx={cx} cy={cy} r={9} fill="#e94560" stroke="#fff" strokeWidth={1.5} />
                        <text x={cx + 12} y={cy + 4} fill="#eee" fontSize="11">{labelOf(c.user_id)}</text>
                    </g>
                );
            })}
        </svg>
    );
}

// ── Relationships: Graph View (vis-network) ─────────────
function GraphView({ data, agentsByUserId, openDetail }) {
    const containerRef = useRef(null);
    const networkRef = useRef(null);
    const agentsRef = useRef(agentsByUserId);
    const openDetailRef = useRef(openDetail);
    agentsRef.current = agentsByUserId;
    openDetailRef.current = openDetail;

    // Stable fingerprint: only rebuild graph when actual data changes
    const dataFingerprint = useMemo(() => {
        const pairKey = data.pairs.map(p => `${p.user_id1}|${p.user_id2}|${p.score.toFixed(4)}`).join(";");
        const memberKey = data.members.map(m => m.user_id).join(";");
        return `${memberKey}||${pairKey}`;
    }, [data]);

    useEffect(() => {
        if (!containerRef.current || !window.vis) return;
        const nodes = data.members.map((m) => {
            const agent = agentsRef.current[m.user_id];
            return {
                id: m.user_id,
                label: agent?.name || m.user_id.slice(0, 6),
                title: agent?.role || "",
            };
        });
        const maxAbs = Math.max(0.001, ...data.pairs.map((p) => Math.abs(p.score)));
        const edges = data.pairs.map((p) => ({
            from: p.user_id1,
            to: p.user_id2,
            value: Math.abs(p.score),
            title: `${p.score > 0 ? "+" : ""}${p.score.toFixed(2)} closeness`,
            width: 1 + (Math.abs(p.score) / maxAbs) * 8,
            color: { color: p.score >= 0 ? "rgba(78, 204, 163, 0.7)" : "rgba(233, 69, 96, 0.7)" },
            dashes: p.score < 0,
        }));
        if (networkRef.current) networkRef.current.destroy();
        networkRef.current = new vis.Network(
            containerRef.current,
            { nodes, edges },
            {
                nodes: {
                    shape: "dot",
                    size: 18,
                    font: { color: "#eee", size: 14 },
                    color: { background: "#e94560", border: "#fff" },
                },
                edges: { smooth: false },
                physics: { stabilization: true, barnesHut: { springLength: 140 } },
            }
        );
        networkRef.current.on("click", (params) => {
            if (params.nodes.length > 0) {
                const uid = params.nodes[0];
                const agent = agentsRef.current[uid];
                openDetailRef.current("user", uid, agent?.name || uid.slice(0, 8));
            }
        });
        return () => {
            if (networkRef.current) {
                networkRef.current.destroy();
                networkRef.current = null;
            }
        };
    }, [dataFingerprint]); // eslint-disable-line react-hooks/exhaustive-deps

    return <div ref={containerRef} style={{ height: 600, width: "100%", background: "#0f0f1e", borderRadius: 6 }}></div>;
}

// ── Relationships Tab ──────────────────────────────────
function RelationshipsTab({ communityId, agentsByUserId, openDetail }) {
    const [data, setData] = useState({ members: [], pairs: [] });
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [viewMode, setViewMode] = useState("heatmap"); // "heatmap" | "graph"

    const fetchCloseness = useCallback(() => {
        if (!communityId) return;
        setRefreshing(true);
        API.get(`/communities/${communityId}/closeness`)
            .then((d) => setData(d))
            .catch(() => setData({ members: [], pairs: [] }))
            .finally(() => { setLoading(false); setRefreshing(false); });
    }, [communityId]);

    useEffect(() => {
        if (!communityId) return;
        setLoading(true);
        fetchCloseness();
    }, [communityId, fetchCloseness]);

    if (loading) return <div className="loading-center"><span className="spinner"></span> Loading relationships...</div>;
    if (data.pairs.length === 0) return <div className="empty-state">No relationships yet — members need to support proposals first</div>;

    const positive = data.pairs.filter((p) => p.score > 0).length;
    const negative = data.pairs.filter((p) => p.score < 0).length;
    const viewProps = { data, agentsByUserId, openDetail };

    return (
        <div className="card">
            <div
                className="card-title"
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
            >
                <span>
                    Member Relationships ({data.members.length} members · {positive} positive · {negative} negative)
                </span>
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    <button
                        className="refresh-btn"
                        onClick={fetchCloseness}
                        disabled={refreshing}
                        title="Refresh relationship data"
                    >
                        {refreshing ? "..." : "Refresh"}
                    </button>
                    {[
                        { id: "heatmap", label: "Heatmap" },
                        { id: "graph", label: "Graph" },
                    ].map((m) => (
                        <button
                            key={m.id}
                            className={`tab-btn ${viewMode === m.id ? "active" : ""}`}
                            onClick={() => setViewMode(m.id)}
                        >
                            {m.label}
                        </button>
                    ))}
                </div>
            </div>
            {viewMode === "heatmap" && <HeatmapView {...viewProps} />}
            {viewMode === "graph"   && <GraphView   {...viewProps} />}
            <div style={{ marginTop: 8, fontSize: "0.8rem", color: "var(--text-muted)" }}>
                Closeness uses per-proposal covariance: agreement on niche proposals scores high, agreement on unanimous proposals scores = 0. Positive = aligned, negative = opposed.
            </div>
        </div>
    );
}

// ── Work Tab (Artifact containers / artifacts) ─────────
const CONTAINER_STATUS_LABEL = { 1: "OPEN", 2: "PENDING_PARENT", 3: "COMMITTED" };
const ARTIFACT_STATUS_LABEL = { 1: "ACTIVE", 2: "SUPERSEDED", 3: "RETIRED" };

function ArtifactNode({ artifact, openDetail, depth }) {
    const [open, setOpen] = useState(false);
    const indent = { marginLeft: `${depth * 1.2}rem` };
    const children = artifact.delegated_to || [];
    return (
        <div style={{ ...indent, marginBottom: "0.4rem", borderLeft: "2px solid #444", paddingLeft: "0.6rem" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "0.4rem" }}>
                <button
                    className="link-btn"
                    onClick={() => setOpen(!open)}
                    style={{ fontSize: "0.7rem" }}
                >
                    {open ? "▼" : "▶"}
                </button>
                <strong style={{ color: "#cfd" }}>{artifact.title || "(untitled)"}</strong>
                <span style={{ fontSize: "0.7rem", color: "#888" }}>
                    [{ARTIFACT_STATUS_LABEL[artifact.status] || artifact.status}]
                </span>
                {artifact.proposal_id && (
                    <button
                        className="link-btn"
                        style={{ fontSize: "0.7rem" }}
                        onClick={() => openDetail({ type: "proposal", id: artifact.proposal_id })}
                    >
                        proposal
                    </button>
                )}
            </div>
            {open && (
                <pre style={{
                    whiteSpace: "pre-wrap",
                    background: "#1a1a1a",
                    padding: "0.5rem",
                    fontSize: "0.75rem",
                    color: "#ddd",
                    margin: "0.3rem 0",
                }}>{artifact.content}</pre>
            )}
            {children.map((c) => (
                <ContainerNode key={c.id} container={c} openDetail={openDetail} depth={depth + 1} bbUserId={null} communityId={null} onRefresh={null} />
            ))}
        </div>
    );
}

function CommitContainerUI({ container, bbUserId, communityId, onDone }) {
    const [ordering, setOrdering] = useState(false);
    const [items, setItems] = useState([]);
    const [dragIdx, setDragIdx] = useState(null);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        const arts = (container.artifacts || []).filter(a => a.status === 1);
        setItems(arts.map(a => ({ id: a.id, title: a.title || "(untitled)" })));
    }, [container]);

    const onDragStart = (i) => setDragIdx(i);
    const onDragOver = (e, i) => {
        e.preventDefault();
        if (dragIdx === null || dragIdx === i) return;
        const newItems = [...items];
        const [moved] = newItems.splice(dragIdx, 1);
        newItems.splice(i, 0, moved);
        setItems(newItems);
        setDragIdx(i);
    };
    const onDragEnd = () => setDragIdx(null);

    const handleCommit = async () => {
        setSubmitting(true);
        try {
            const orderedIds = items.map(it => it.id);
            const proposal = await API.post(
                `/communities/${communityId}/proposals`,
                {
                    user_id: bbUserId,
                    proposal_type: "CommitArtifact",
                    proposal_text: `Commit container "${container.title}" with ${orderedIds.length} artifacts`,
                    val_uuid: container.id,
                    val_text: JSON.stringify(orderedIds),
                }
            );
            await API.patch(`/proposals/${proposal.id}/submit`);
            await API.post(`/proposals/${proposal.id}/support`, { user_id: bbUserId });
            onDone?.();
        } catch (e) {
            alert("CommitArtifact failed: " + e);
        } finally {
            setSubmitting(false);
            setOrdering(false);
        }
    };

    if (!ordering) {
        const arts = (container.artifacts || []).filter(a => a.status === 1);
        const allFilled = arts.length > 0 && arts.every(a => (a.content || "").trim());
        if (!allFilled || !bbUserId) return null;
        return (
            <button onClick={() => setOrdering(true)}
                style={{ marginTop: "0.5rem", fontSize: "0.75rem", background: "#4ecca3", border: "none", padding: "0.3rem 0.8rem", borderRadius: 4, cursor: "pointer", color: "#111" }}>
                📦 Commit Container…
            </button>
        );
    }

    return (
        <div style={{ marginTop: "0.5rem", padding: "0.5rem", border: "1px solid #4ecca3", borderRadius: 6, background: "#0e1a0e" }}>
            <div style={{ fontWeight: 600, marginBottom: "0.4rem", color: "#4ecca3", fontSize: "0.85rem" }}>
                Drag to set commit order:
            </div>
            {items.map((item, i) => (
                <div key={item.id}
                    draggable
                    onDragStart={() => onDragStart(i)}
                    onDragOver={(e) => onDragOver(e, i)}
                    onDragEnd={onDragEnd}
                    style={{
                        padding: "0.35rem 0.5rem",
                        margin: "0.2rem 0",
                        background: dragIdx === i ? "#1a3a1a" : "#141414",
                        border: "1px solid #333",
                        borderRadius: 3,
                        cursor: "grab",
                        fontSize: "0.8rem",
                        display: "flex",
                        gap: "0.5rem",
                    }}>
                    <span style={{ color: "#888" }}>{i + 1}.</span>
                    <span>{item.title}</span>
                </div>
            ))}
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
                <button onClick={handleCommit} disabled={submitting}
                    style={{ background: "#4ecca3", border: "none", padding: "0.3rem 0.8rem", borderRadius: 4, cursor: "pointer", color: "#111", fontSize: "0.75rem" }}>
                    {submitting ? "Committing…" : "Commit"}
                </button>
                <button onClick={() => setOrdering(false)}
                    style={{ background: "#333", border: "none", padding: "0.3rem 0.8rem", borderRadius: 4, cursor: "pointer", color: "#ccc", fontSize: "0.75rem" }}>
                    Cancel
                </button>
            </div>
        </div>
    );
}

function ContainerNode({ container, openDetail, depth, bbUserId, communityId, onRefresh }) {
    const [open, setOpen] = useState(true);
    const indent = { marginLeft: `${depth * 0.6}rem` };
    const arts = container.artifacts || [];
    if (container.cycle) {
        return <div style={indent}>↻ cycle to {container.title}</div>;
    }
    return (
        <div style={{ ...indent, marginBottom: "0.6rem", border: "1px solid #333", borderRadius: 4, padding: "0.5rem", background: "#181818" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem" }}>
                <button className="link-btn" onClick={() => setOpen(!open)} style={{ fontSize: "0.75rem" }}>
                    {open ? "▼" : "▶"}
                </button>
                <strong>📦 {container.title}</strong>
                <span style={{ fontSize: "0.7rem", color: "#fa0" }}>
                    {CONTAINER_STATUS_LABEL[container.status] || container.status}
                </span>
                <span style={{ fontSize: "0.7rem", color: "#888" }}>
                    {arts.length} artifact{arts.length === 1 ? "" : "s"}
                </span>
            </div>
            {open && (
                <div style={{ marginTop: "0.4rem" }}>
                    {arts.length === 0 && <div style={{ color: "#666", fontSize: "0.75rem" }}>(empty)</div>}
                    {arts.map((a) => (
                        <ArtifactNode key={a.id} artifact={a} openDetail={openDetail} depth={depth + 1} />
                    ))}
                    {container.committed_content && container.status === 3 && (
                        <details style={{ marginTop: "0.4rem" }}>
                            <summary style={{ fontSize: "0.75rem", color: "#9c9" }}>committed content</summary>
                            <pre style={{
                                whiteSpace: "pre-wrap",
                                background: "#0e1a0e",
                                padding: "0.5rem",
                                fontSize: "0.75rem",
                                color: "#cfc",
                            }}>{container.committed_content}</pre>
                        </details>
                    )}
                    {container.status === 1 && (
                        <CommitContainerUI container={container} bbUserId={bbUserId} communityId={communityId} onDone={onRefresh} />
                    )}
                </div>
            )}
        </div>
    );
}

function WorkTab({ communityId, openDetail, bbUserId }) {
    const [tree, setTree] = useState([]);
    const [loading, setLoading] = useState(false);
    const [view, setView] = useState("tree");
    const [error, setError] = useState(null);

    useEffect(() => {
        if (!communityId) return;
        setLoading(true);
        setError(null);
        API.get(`/artifacts/communities/${communityId}/work_tree`)
            .then((d) => setTree(Array.isArray(d) ? d : []))
            .catch((e) => { setError(String(e)); setTree([]); })
            .finally(() => setLoading(false));
    }, [communityId]);

    // Manuscript view: flatten the FIRST root container's artifacts (in stored order).
    const manuscript = useMemo(() => {
        if (!tree.length) return "";
        const root = tree[0];
        const arts = root.artifacts || [];
        return arts.map((a) => `## ${a.title || "(untitled)"}\n\n${a.content}`).join("\n\n---\n\n");
    }, [tree]);

    // Open work view: collect all OPEN containers recursively.
    const openContainers = useMemo(() => {
        const out = [];
        const walk = (c) => {
            if (!c || c.cycle) return;
            if (c.status === 1) out.push(c);
            (c.artifacts || []).forEach((a) => (a.delegated_to || []).forEach(walk));
        };
        tree.forEach(walk);
        return out;
    }, [tree]);

    return (
        <div className="card">
            <div style={{ display: "flex", gap: "0.4rem", marginBottom: "0.6rem" }}>
                {[
                    { id: "tree", label: "Tree" },
                    { id: "manuscript", label: "Manuscript" },
                    { id: "open", label: "Open Work" },
                ].map((m) => (
                    <button
                        key={m.id}
                        className={`tab-btn ${view === m.id ? "active" : ""}`}
                        onClick={() => setView(m.id)}
                    >
                        {m.label}
                    </button>
                ))}
            </div>
            {loading && <div>Loading…</div>}
            {error && <div style={{ color: "#f88" }}>Error: {error}</div>}
            {!loading && !error && tree.length === 0 && (
                <div style={{ color: "#888" }}>No artifact containers in this community.</div>
            )}
            {view === "tree" && tree.map((c) => (
                <ContainerNode key={c.id} container={c} openDetail={openDetail} depth={0}
                    bbUserId={bbUserId} communityId={communityId}
                    onRefresh={() => API.get(`/artifacts/communities/${communityId}/work_tree`).then(d => setTree(Array.isArray(d) ? d : [])).catch(() => {})} />
            ))}
            {view === "manuscript" && (
                <pre style={{
                    whiteSpace: "pre-wrap",
                    background: "#0e0e0e",
                    padding: "1rem",
                    fontSize: "0.85rem",
                    color: "#eee",
                    lineHeight: 1.5,
                }}>{manuscript || "(no content yet)"}</pre>
            )}
            {view === "open" && (
                <div>
                    <div style={{ marginBottom: "0.5rem", color: "#9c9" }}>
                        {openContainers.length} open container{openContainers.length === 1 ? "" : "s"}
                    </div>
                    {openContainers.map((c) => (
                        <div key={c.id} style={{ padding: "0.4rem", borderBottom: "1px solid #222" }}>
                            <strong>{c.title}</strong>{" "}
                            <span style={{ color: "#888", fontSize: "0.75rem" }}>
                                ({(c.artifacts || []).length} artifacts)
                            </span>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Tab Navigation ──────────────────────────────────────
function TabNav({ activeTab, setActiveTab }) {
    const tabs = [
        { id: "dashboard", label: "Dashboard" },
        { id: "agents", label: "Agents" },
        { id: "variables", label: "Variables" },
        { id: "statements", label: "Statements" },
        { id: "pulses", label: "Pulses" },
        { id: "actions", label: "Action Tree" },
        { id: "work", label: "Work" },
        { id: "chat", label: "Chat" },
        { id: "interview", label: "Interview" },
        { id: "timeline", label: "Timeline" },
        { id: "relationships", label: "Relationships" },
    ];
    return (
        <div className="tab-nav">
            {tabs.map((t) => (
                <button
                    key={t.id}
                    className={`tab-btn ${activeTab === t.id ? "active" : ""}`}
                    onClick={() => setActiveTab(t.id)}
                >
                    {t.label}
                </button>
            ))}
        </div>
    );
}

// ── Variables Widget ────────────────────────────────────
function VariablesWidget({ communityId, openDetail }) {
    const [variables, setVariables] = useState([]);
    const [loading, setLoading] = useState(false);
    const [expanded, setExpanded] = useState(false);

    useEffect(() => {
        if (!communityId) return;
        setLoading(true);
        API.getCached(`/communities/${communityId}/variables`, 10000)
            .then(v => setVariables(parseVariables(v)))
            .catch(() => setVariables([]))
            .finally(() => setLoading(false));
    }, [communityId]);

    if (!communityId) return null;
    if (loading && variables.length === 0) return (
        <div className="vars-widget">
            <div className="vars-widget-header"><span className="vars-widget-title">Governance Variables</span></div>
            <div style={{ padding: "8px 0", color: "var(--text-muted)", fontSize: "0.8rem" }}>Loading...</div>
        </div>
    );
    if (!loading && variables.length === 0) return null;

    // Key governance vars always shown
    const KEY_VARS = ["PulseSupport", "ProposalSupport", "Membership", "ThrowOut", "MaxAge",
                      "MembershipHandler", "proposalCooldown"];
    const keyVars = variables.filter(v => KEY_VARS.includes(v.name));
    const allVars = expanded ? variables : (keyVars.length > 0 ? keyVars : variables.slice(0, 6));

    return (
        <div className="vars-widget">
            <div className="vars-widget-header">
                <span className="vars-widget-title">Governance Variables</span>
                <span className="vars-widget-action clickable"
                      onClick={(e) => { e.stopPropagation(); communityId && openDetail("community", communityId, "Community"); }}>
                    Full Detail ↗
                </span>
                <span className="vars-widget-action clickable" onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}>
                    {expanded ? "Show Less" : `Show All (${variables.length})`}
                </span>
            </div>
            <div className="vars-inline-grid">
                {allVars.map(v => (
                    <div key={v.name} className="var-inline-item clickable"
                         onClick={(e) => { e.stopPropagation(); openDetail("variable", `${communityId}|${v.name}`, v.name); }}>
                        <span className="var-inline-name">{v.name}</span>
                        <span className="var-inline-value">{v.value}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Community Overview ──────────────────────────────────
function CommunityOverview({ status, pulses, openDetail, communityId, overrideCommunity }) {
    // When viewing an action, show its community data instead of root
    const community = overrideCommunity || status?.community;
    const nextPulse = pulses?.find((p) => p.status === 0);
    const donePulses = pulses?.filter((p) => p.status === 2) || [];

    const supportCount = nextPulse?.support_count || 0;
    const threshold = nextPulse?.threshold || 1;
    const pct = Math.min(100, Math.round((supportCount / threshold) * 100));

    return (
        <div className="card overview">
            {overrideCommunity ? (
                <div className="community-title-banner action">
                    <div className="community-title-label">Action Community</div>
                    <div className="community-title-name">{overrideCommunity.name}</div>
                </div>
            ) : (
                <div className="community-title-banner root">
                    <div className="community-title-label">Root Community</div>
                    <div className="community-title-name">{community?.name || "Loading..."}</div>
                </div>
            )}
            <div className="card-title">Community Overview</div>
            <div className="overview-grid">
                <div className="overview-stat clickable" onClick={() => community && openDetail("community", community.id, community.name)}>
                    <div className="stat-value">{community?.member_count || 0}</div>
                    <div className="stat-label">Members</div>
                </div>
                <div className="overview-stat">
                    <div className="stat-value">{status?.round || 0}</div>
                    <div className="stat-label">Round</div>
                </div>
                <div className="overview-stat clickable"
                     onClick={() => nextPulse && openDetail("pulse", nextPulse.id, "Next Pulse")}>
                    <div className="stat-value">{donePulses.length}</div>
                    <div className="stat-label">Pulses Done</div>
                </div>
                <div className="overview-stat">
                    <div className="stat-value">{status?.total_events || 0}</div>
                    <div className="stat-label">Total Events</div>
                </div>
            </div>
            <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                    Next Pulse Progress
                </div>
                <div className="pulse-bar" style={{ cursor: nextPulse ? "pointer" : "default" }}
                     onClick={() => nextPulse && openDetail("pulse", nextPulse.id, "Next Pulse")}>
                    <div className="pulse-bar-fill" style={{ width: `${pct}%` }}></div>
                    <div className="pulse-bar-text">
                        {supportCount} / {threshold} ({pct}%)
                    </div>
                </div>
            </div>
            <VariablesWidget communityId={communityId} openDetail={openDetail} />
        </div>
    );
}

// ── Activity Feed ───────────────────────────────────────
function ActivityFeed({ events, openDetail, agentsByUserId, activeCommunityId, rootCommunityId }) {
    const feedRef = useRef(null);
    const [autoScroll, setAutoScroll] = useState(true);

    useEffect(() => {
        if (autoScroll && feedRef.current) {
            feedRef.current.scrollTop = 0;
        }
    }, [events, autoScroll]);

    function handleScroll() {
        if (feedRef.current) {
            setAutoScroll(feedRef.current.scrollTop < 10);
        }
    }

    const byCommunity = activeCommunityId
        ? (events || []).filter(ev => ev.community_id === activeCommunityId)
        : (events || []).filter(ev => !ev.community_id || ev.community_id === rootCommunityId);
    // Filter out noise: failed do_nothing events (guards, already-supported, etc.)
    const filtered = byCommunity.filter(ev => !(ev.success === false && ev.action === "do_nothing"));
    // Newest first, capped at 100
    const newest = filtered.slice(-100).reverse();

    return (
        <div className="card">
            <div className="card-title">Activity Feed ({newest.length} events)</div>
            <div className="activity-feed" ref={feedRef} onScroll={handleScroll}>
                {newest.length === 0 && (
                    <div className="empty-state">Waiting for simulation to start...</div>
                )}
                {newest.map((ev, i) => {
                    // Determine clickable entity type from action
                    const refType = ev.action === 'send_chat' ? null
                        : ev.action === 'edit_artifact' ? 'artifact'
                        : 'proposal';
                    const isClickable = !!(ev.ref_id && openDetail);
                    return (
                        <div className={`event-item ${isClickable ? 'clickable' : ''}`}
                             key={`${ev.time}-${i}`}
                             onClick={isClickable ? () => openDetail(refType, ev.ref_id, (ev.details || '').slice(0, 40)) : undefined}>
                            <span className="event-time">{formatTime(ev.time)}</span>
                            <span className={`event-agent ${agentColor(ev.agent)} entity-link`}
                                  onClick={(e) => {
                                      e.stopPropagation();
                                      const agent = Object.values(agentsByUserId || {}).find(a => a.name === ev.agent);
                                      if (agent) openDetail("user", agent.user_id, ev.agent);
                                  }}>
                                {ev.agent}
                            </span>
                            <span className={`event-badge badge-${ev.action}`}>{ev.action}</span>
                            <div className="event-details">
                                {ev.details && <span>{ev.details}</span>}
                                {ev.reason && <div className="event-reason">{ev.reason}</div>}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ── Proposal Card ──────────────────────────────────────
// Defined at module level (not inside ProposalBoard) so React sees a stable
// component identity across re-renders.  Defining it inside ProposalBoard would
// create a new function reference on every render → React unmounts/remounts
// every card every poll cycle → useProposalCardTitle resets → visible "jump".
function ProposalCard({ p, memberCount, proposalThreshold, getTypeThreshold, openDetail }) {
    const cardTitle = useProposalCardTitle(p);
    const statusClass = p.proposal_status === "Accepted" ? "accepted" : p.proposal_status === "Rejected" ? "rejected" : "";
    const hasEnoughSupport = memberCount > 0 && (
        p.proposal_status === "OutThere"
            ? p.support_count >= proposalThreshold
            : p.proposal_status === "OnTheAir"
                ? p.support_count >= getTypeThreshold(p.proposal_type)
                : false
    );
    const readyClass = hasEnoughSupport ? "ready-to-pass" : "";
    return (
        <div className={`proposal-card ${statusClass} ${readyClass} clickable`}
             onClick={() => openDetail("proposal", p.id, `${p.proposal_type}`)}>
            <div className="proposal-type">{p.proposal_type}</div>
            <div className="proposal-text">{cardTitle}</div>
            <div className="proposal-meta">
                <span>Support: {p.support_count}{memberCount > 0 && (
                    p.proposal_status === "OutThere"
                        ? `/${proposalThreshold}`
                        : p.proposal_status === "OnTheAir"
                            ? `/${getTypeThreshold(p.proposal_type)}`
                            : ""
                )}</span>
                <span>Age: {p.age}</span>
                {hasEnoughSupport && <span className="ready-badge">Ready</span>}
            </div>
        </div>
    );
}

// ── Proposal Board ──────────────────────────────────────
function ProposalBoard({ proposals, openDetail, pulses, status, activeCommunity }) {
    const onTheAir = proposals?.filter((p) => p.proposal_status === "OnTheAir") || [];
    const outThere = proposals?.filter((p) => p.proposal_status === "OutThere") || [];

    // Find last executed pulse and its decided proposals
    const donePulses = (pulses || []).filter(p => p.status === 2);
    const lastDonePulse = donePulses.length > 0
        ? donePulses.reduce((a, b) => new Date(a.created_at) > new Date(b.created_at) ? a : b)
        : null;
    const recent = lastDonePulse
        ? (proposals || []).filter(p =>
            p.pulse_id === lastDonePulse.id &&
            (p.proposal_status === "Accepted" || p.proposal_status === "Rejected"))
        : [];
    const accepted = recent.filter(p => p.proposal_status === "Accepted");
    const rejected = recent.filter(p => p.proposal_status === "Rejected");

    // Fetch variables for the active community (action sub-community or root)
    const [communityVars, setCommunityVars] = useState({});
    const communityForThresholds = activeCommunity || status?.community;
    const effectiveCommunityId = communityForThresholds?.id;

    useEffect(() => {
        if (!effectiveCommunityId) return;
        // If activeCommunity is set, fetch its variables; otherwise use root's from status
        if (activeCommunity) {
            API.getCached(`/communities/${effectiveCommunityId}/variables`, 10000)
                .then(v => {
                    const parsed = typeof v === "object" && v.variables ? v.variables : v;
                    setCommunityVars(typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {});
                })
                .catch(() => setCommunityVars({}));
        } else {
            setCommunityVars(status?.community?.variables || {});
        }
    }, [effectiveCommunityId, activeCommunity, status]);

    // Calculate support thresholds from the ACTIVE community (not always root)
    const memberCount = communityForThresholds?.member_count || 0;
    const vars = communityVars;
    const proposalSupportPct = parseInt(vars["ProposalSupport"] || "25", 10);
    // For OnTheAir: each proposal type has its own threshold variable
    const getTypeThreshold = (pType) => {
        const pct = parseInt(vars[pType] || vars["ProposalSupport"] || "25", 10);
        return Math.max(1, Math.ceil(memberCount * pct / 100));
    };
    const proposalThreshold = Math.max(1, Math.ceil(memberCount * proposalSupportPct / 100));

    return (
        <div className="card">
            <div className="card-title">Proposals</div>
            <div className="proposal-columns">
                <div>
                    <div className="proposal-column-title">On The Air ({onTheAir.length})</div>
                    {onTheAir.map((p) => <ProposalCard key={p.id} p={p} memberCount={memberCount} proposalThreshold={proposalThreshold} getTypeThreshold={getTypeThreshold} openDetail={openDetail} />)}
                    {onTheAir.length === 0 && <div className="empty-state">None</div>}
                </div>
                <div>
                    <div className="proposal-column-title">Out There ({outThere.length})</div>
                    {outThere.map((p) => <ProposalCard key={p.id} p={p} memberCount={memberCount} proposalThreshold={proposalThreshold} getTypeThreshold={getTypeThreshold} openDetail={openDetail} />)}
                    {outThere.length === 0 && <div className="empty-state">None</div>}
                </div>
                <div>
                    <div className="proposal-column-title">Last Pulse Results ({recent.length})</div>
                    {accepted.length > 0 && (
                        <div style={{ marginBottom: 8 }}>
                            <div className="results-sub accepted">Accepted ({accepted.length})</div>
                            {accepted.map((p) => <ProposalCard key={p.id} p={p} memberCount={memberCount} proposalThreshold={proposalThreshold} getTypeThreshold={getTypeThreshold} openDetail={openDetail} />)}
                        </div>
                    )}
                    {rejected.length > 0 && (
                        <div>
                            <div className="results-sub rejected">Rejected ({rejected.length})</div>
                            {rejected.map((p) => <ProposalCard key={p.id} p={p} memberCount={memberCount} proposalThreshold={proposalThreshold} getTypeThreshold={getTypeThreshold} openDetail={openDetail} />)}
                        </div>
                    )}
                    {recent.length === 0 && <div className="empty-state">No pulse results yet</div>}
                </div>
            </div>
        </div>
    );
}

// ── Dashboard Tab ───────────────────────────────────────

function DashboardTab({ status, events, proposals, pulses, onRunRound, runningRound, paused, onTogglePause, onRestart, restarting, openDetail, agentsByUserId, communityId, activeCommunity, activeCommunityId, rootCommunityId }) {
    if (restarting) {
        return (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 80, gap: 16 }}>
                <span className="spinner" style={{ width: 36, height: 36, borderWidth: 4 }}></span>
                <div style={{ color: "var(--text-secondary)", fontSize: "1.1rem" }}>Restarting simulation loop…</div>
                <div style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>All data is preserved. Agents will resume shortly.</div>
            </div>
        );
    }
    return (
        <div>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12, gap: 8 }}>
                <button
                    className="run-round-btn restart-btn"
                    onClick={onRestart}
                    title="Restart the simulation loop — all data and history are preserved"
                >
                    ↺ Restart
                </button>
                <button
                    className={`run-round-btn ${paused ? "paused" : ""}`}
                    onClick={onTogglePause}
                >
                    {paused ? "Resume" : "Pause"}
                </button>
                <button className="run-round-btn" onClick={onRunRound} disabled={runningRound}>
                    {runningRound ? <><span className="spinner"></span> Running...</> : "Run Round"}
                </button>
            </div>
            <div className="dashboard-grid">
                <CommunityOverview status={status} pulses={pulses} openDetail={openDetail} communityId={communityId} overrideCommunity={activeCommunity} />
                <ActivityFeed events={events} openDetail={openDetail} agentsByUserId={agentsByUserId} activeCommunityId={activeCommunityId} rootCommunityId={rootCommunityId} />
                <ProposalBoard proposals={proposals} openDetail={openDetail} pulses={pulses} status={status} activeCommunity={activeCommunity} />
            </div>
        </div>
    );
}

// ── Eagerness Bar ──────────────────────────────────────
function EagernessBar({ eagerness, eagerFront }) {
    const colors = {
        propose: "#e94560",
        pulse: "#f0c040",
        comment: "#90caf9",
        support: "#4ecca3",
        observe: "#555",
    };
    const color = colors[eagerFront] || "#555";
    const pct = ((eagerness || 5) / 10) * 100;
    return (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", minWidth: 76 }}>
            <div style={{ fontSize: "0.65rem", color, fontWeight: 700, marginBottom: 2, textTransform: "uppercase" }}>
                {eagerFront || "observe"}
            </div>
            <div style={{ width: 76, height: 4, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2, transition: "width 0.4s ease" }} />
            </div>
            <div style={{ fontSize: "0.6rem", color: "var(--text-muted)", marginTop: 2 }}>
                eagerness {eagerness || 5}/10
            </div>
        </div>
    );
}

// ── Traits Radar Chart ──────────────────────────────────
function TraitsRadarChart({ traits, agentName }) {
    const canvasRef = useRef(null);
    const chartRef = useRef(null);

    useEffect(() => {
        if (!canvasRef.current || !traits) return;

        if (chartRef.current) {
            chartRef.current.destroy();
        }

        const labels = Object.keys(traits);
        const values = Object.values(traits).map((v) => Math.round(v * 100));

        chartRef.current = new Chart(canvasRef.current, {
            type: "radar",
            data: {
                labels: labels.map((l) => l.replace("_", " ")),
                datasets: [
                    {
                        label: agentName,
                        data: values,
                        backgroundColor: "rgba(233, 69, 96, 0.2)",
                        borderColor: "rgba(233, 69, 96, 0.8)",
                        borderWidth: 2,
                        pointBackgroundColor: "rgba(233, 69, 96, 1)",
                        pointRadius: 3,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                scales: {
                    r: {
                        beginAtZero: true,
                        max: 100,
                        ticks: {
                            stepSize: 25,
                            color: "#666",
                            backdropColor: "transparent",
                        },
                        grid: { color: "rgba(255,255,255,0.1)" },
                        angleLines: { color: "rgba(255,255,255,0.1)" },
                        pointLabels: { color: "#ccc", font: { size: 11 } },
                    },
                },
                plugins: {
                    legend: { display: false },
                },
            },
        });

        return () => {
            if (chartRef.current) chartRef.current.destroy();
        };
    }, [traits, agentName]);

    return (
        <div className="radar-container">
            <canvas ref={canvasRef}></canvas>
        </div>
    );
}

// ── Agent Card ──────────────────────────────────────────
function AgentCard({ agent, expanded, onToggle, openDetail }) {
    return (
        <div className={`agent-card ${expanded ? "expanded" : ""}`} onClick={onToggle}>
            <div className="agent-header">
                <div>
                    <div className={`agent-name ${agentColor(agent.name)}`}>{agent.name}</div>
                    <div className="agent-role">{agent.role}</div>
                </div>
                <EagernessBar eagerness={agent.eagerness} eagerFront={agent.eager_front} />
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div className="agent-actions-count">{agent.actions_taken} actions</div>
                    <button className="mini-view-btn" onClick={(e) => { e.stopPropagation(); openDetail("user", agent.user_id, agent.name); }}>
                        View
                    </button>
                </div>
            </div>
            <div className="agent-background">
                {expanded ? agent.background : (agent.background || "").slice(0, 120) + "..."}
            </div>
            {expanded && (
                <div className="agent-detail-grid">
                    <TraitsRadarChart traits={agent.traits} agentName={agent.name} />
                    <div>
                        <div style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: 8, color: "var(--text-secondary)" }}>
                            Recent Actions
                        </div>
                        <div className="action-list">
                            {(agent.recent_actions || []).slice().reverse().map((a, i) => (
                                <div className="action-item" key={i}>
                                    <div>
                                        <span className="action-type">{a.action}</span>
                                        <span className="action-time" style={{ marginLeft: 8 }}>{formatTime(a.time)}</span>
                                    </div>
                                    <div className="action-detail">{a.details}</div>
                                    {a.reason && <div className="action-detail" style={{ fontStyle: "italic" }}>{a.reason}</div>}
                                </div>
                            ))}
                            {(!agent.recent_actions || agent.recent_actions.length === 0) && (
                                <div className="empty-state">No actions yet</div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ── Agents Tab ──────────────────────────────────────────
function AgentsTab({ agents, openDetail, communityId, rootCommunityId }) {
    const [expandedAgent, setExpandedAgent] = useState(null);
    const [memberIds, setMemberIds] = useState(null);

    useEffect(() => {
        if (!communityId || communityId === rootCommunityId) {
            setMemberIds(null);
            return;
        }
        API.get(`/communities/${communityId}/members`)
            .then(m => setMemberIds(new Set(m.map(x => x.user_id))))
            .catch(() => setMemberIds(null));
    }, [communityId, rootCommunityId]);

    const filtered = memberIds
        ? (agents || []).filter(a => memberIds.has(a.user_id))
        : (agents || []);

    return (
        <div className="agent-grid">
            {filtered.map((a) => (
                <AgentCard
                    key={a.name}
                    agent={a}
                    expanded={expandedAgent === a.name}
                    onToggle={() => setExpandedAgent(expandedAgent === a.name ? null : a.name)}
                    openDetail={openDetail}
                />
            ))}
            {filtered.length === 0 && (
                <div className="empty-state">{memberIds ? "No agents in this community" : "No agents registered yet"}</div>
            )}
        </div>
    );
}

// ── Chat Tab ────────────────────────────────────────────
function ChatTab({ communityId, communityName, agentsByUserId, bbUserId }) {
    const [messages, setMessages] = React.useState([]);
    const [draft, setDraft] = React.useState("");
    const [sending, setSending] = React.useState(false);
    const textareaRef = React.useRef(null);

    // Keep a ref so handleSend always reads the LATEST communityId even if the
    // closure captured a stale value (e.g. during a concurrent-mode render).
    const communityIdRef = React.useRef(communityId);
    React.useEffect(() => { communityIdRef.current = communityId; }, [communityId]);

    // Force-refresh bypasses cache
    const loadMessages = React.useCallback(() => {
        if (!communityId) return;
        const url = `/entities/community/${communityId}/comments?limit=100`;
        delete _cache[url];
        API.getCached(url).then(setMessages).catch(() => {});
    }, [communityId]);

    React.useEffect(() => {
        if (!communityId) return;
        loadMessages();
        const iv = setInterval(loadMessages, 5000);
        return () => clearInterval(iv);
    }, [communityId, loadMessages]);

    async function handleSend() {
        const text = draft.trim();
        if (!text || sending) return;
        // Always read from ref — guaranteed to be the current communityId
        const targetId = communityIdRef.current;
        if (!targetId) { alert("No community selected — cannot send message."); return; }
        setSending(true);
        try {
            await API.post("/simulation/chat", { message: text, community_id: targetId });
            setDraft("");
            setTimeout(loadMessages, 300);
        } catch (err) {
            alert(`Could not send message: ${err.message}`);
        } finally {
            setSending(false);
            textareaRef.current?.focus();
        }
    }

    function handleKeyDown(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            handleSend();
        }
    }

    // Sort newest-first so latest messages appear at the top
    const sorted = [...messages].sort(
        (a, b) => new Date(b.created_at) - new Date(a.created_at)
    );

    const scopeLabel = communityName || (communityId ? communityId.slice(0, 8) : "…");

    return (
        <div className="chat-tab">
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
                <h3 style={{ margin: 0, color: "#e0e0e0" }}>Community Chat</h3>
                <span className="chat-scope-badge">📍 {scopeLabel}</span>
            </div>

            {/* ── Compose box ── */}
            <div className="bb-compose">
                <div className="bb-compose-label">👁 Big Brother → <em>{scopeLabel}</em></div>
                <textarea
                    ref={textareaRef}
                    className="bb-compose-input"
                    placeholder={`Send a message to ${scopeLabel}… (Ctrl+Enter to send)`}
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                    onKeyDown={handleKeyDown}
                    rows={2}
                    disabled={sending}
                />
                <button
                    className="bb-compose-btn"
                    onClick={handleSend}
                    disabled={!draft.trim() || sending}
                >
                    {sending ? "Sending…" : "Send"}
                </button>
            </div>

            {/* ── Messages ── */}
            {sorted.length === 0 && (
                <div className="empty-state">No chat messages yet. Agents will start chatting soon...</div>
            )}
            <div className="chat-messages">
                {sorted.map((m) => {
                    const isBB = bbUserId && m.user_id === bbUserId;
                    const agent = agentsByUserId?.[m.user_id];
                    const name = isBB ? "👁 Big Brother" : (agent?.name || (m.user_id || "").slice(0, 8));
                    const colorClass = isBB ? "bb-message-author" : (agent ? agentColor(name) : "");
                    return (
                        <div key={m.id} className={`chat-message${isBB ? " bb-message" : ""}`}>
                            <div className="chat-message-header">
                                <span className={`chat-author ${colorClass}`}>{name}</span>
                                <span className="chat-time">{formatTime(m.created_at)}</span>
                            </div>
                            <div className="chat-text">{m.comment_text}</div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ── Interview Tab ───────────────────────────────────────
function InterviewTab({ agents }) {
    const [selectedAgent, setSelectedAgent] = useState(null);
    const [question, setQuestion] = useState("");
    const [conversations, setConversations] = useState({});
    const [loading, setLoading] = useState(false);
    const chatEndRef = useRef(null);

    useEffect(() => {
        if (chatEndRef.current) {
            chatEndRef.current.scrollIntoView({ behavior: "smooth" });
        }
    }, [conversations, selectedAgent]);

    const currentChat = selectedAgent ? (conversations[selectedAgent] || []) : [];

    async function handleSend() {
        if (!selectedAgent || !question.trim() || loading) return;

        const q = question.trim();
        setQuestion("");
        setLoading(true);

        setConversations((prev) => ({
            ...prev,
            [selectedAgent]: [...(prev[selectedAgent] || []), { type: "question", text: q }],
        }));

        try {
            const res = await API.post("/simulation/interview", {
                agent_name: selectedAgent,
                question: q,
            });
            setConversations((prev) => ({
                ...prev,
                [selectedAgent]: [...(prev[selectedAgent] || []), { type: "answer", text: res.answer }],
            }));
        } catch (err) {
            setConversations((prev) => ({
                ...prev,
                [selectedAgent]: [...(prev[selectedAgent] || []), { type: "answer", text: `Error: ${err.message}` }],
            }));
        } finally {
            setLoading(false);
        }
    }

    function handleKeyDown(e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    }

    return (
        <div className="interview-container">
            <div className="agent-selector">
                <div className="card-title">Select Agent</div>
                {(agents || []).map((a) => (
                    <button
                        key={a.name}
                        className={`agent-select-btn ${selectedAgent === a.name ? "selected" : ""}`}
                        onClick={() => setSelectedAgent(a.name)}
                    >
                        <div className={`name ${agentColor(a.name)}`}>{a.name}</div>
                        <div className="role">{a.role}</div>
                    </button>
                ))}
            </div>
            <div className="chat-area">
                <div className="chat-messages">
                    {!selectedAgent && (
                        <div className="empty-state">Select an agent to start an interview</div>
                    )}
                    {currentChat.map((msg, i) => (
                        <div key={i} className={`chat-bubble ${msg.type}`}>
                            <div className="bubble-label">
                                {msg.type === "question" ? "You" : selectedAgent}
                            </div>
                            {msg.text}
                        </div>
                    ))}
                    {loading && (
                        <div className="chat-bubble answer">
                            <div className="bubble-label">{selectedAgent}</div>
                            <span className="spinner"></span> Thinking...
                        </div>
                    )}
                    <div ref={chatEndRef} />
                </div>
                <div className="chat-input-row">
                    <input
                        className="chat-input"
                        type="text"
                        placeholder={selectedAgent ? `Ask ${selectedAgent} a question...` : "Select an agent first"}
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        onKeyDown={handleKeyDown}
                        disabled={!selectedAgent || loading}
                    />
                    <button className="send-btn" onClick={handleSend} disabled={!selectedAgent || !question.trim() || loading}>
                        Send
                    </button>
                </div>
            </div>
        </div>
    );
}

// ── Timeline Tab ────────────────────────────────────────
function TimelineTab({ pulses, proposals, events, openDetail, agentsByUserId, activeCommunityId, rootCommunityId }) {
    const [filterKind, setFilterKind] = useState("all");   // all | pulse | proposal | event
    const [filterMember, setFilterMember] = useState("");  // agent name or ""
    const [filterKeyword, setFilterKeyword] = useState(""); // free-text search

    // Merge events into one chronological stream
    // Entries: pulse markers + proposal events + agent action events
    const eventsByCommunity = activeCommunityId
        ? (events || []).filter(ev => ev.community_id === activeCommunityId)
        : (events || []).filter(ev => !ev.community_id || ev.community_id === rootCommunityId);
    // Filter out noise: failed do_nothing events (guards, already-supported, etc.)
    const filteredEvents = eventsByCommunity.filter(ev => !(ev.success === false && ev.action === "do_nothing"));

    const allEntries = [];

    (pulses || []).forEach(p => {
        allEntries.push({ kind: "pulse", time: p.created_at, data: p });
    });

    (proposals || []).forEach(p => {
        allEntries.push({ kind: "proposal", time: p.created_at, data: p });
    });

    filteredEvents.forEach(ev => {
        allEntries.push({ kind: "event", time: ev.time, data: ev });
    });

    allEntries.sort((a, b) => new Date(b.time) - new Date(a.time));

    // Derive unique agent/member names for the member dropdown
    const memberNames = useMemo(() => {
        const names = new Set();
        filteredEvents.forEach(ev => { if (ev.agent) names.add(ev.agent); });
        (proposals || []).forEach(p => {
            const a = agentsByUserId?.[p.user_id];
            if (a?.name) names.add(a.name);
        });
        return [...names].sort();
    }, [filteredEvents, proposals, agentsByUserId]);

    // Apply filters
    const kw = filterKeyword.trim().toLowerCase();
    const entries = allEntries.filter(entry => {
        // --- kind filter ---
        if (filterKind !== "all" && entry.kind !== filterKind) return false;

        // --- member filter ---
        if (filterMember) {
            if (entry.kind === "event") {
                if (entry.data.agent !== filterMember) return false;
            } else if (entry.kind === "proposal") {
                const a = agentsByUserId?.[entry.data.user_id];
                if ((a?.name || "") !== filterMember) return false;
            } else {
                // pulse entries have no author → hide when member filter is active
                return false;
            }
        }

        // --- keyword filter ---
        if (kw) {
            const d = entry.data;
            let haystack = "";
            if (entry.kind === "pulse") {
                haystack = `pulse ${d.status}`;
            } else if (entry.kind === "proposal") {
                haystack = [d.proposal_type, d.proposal_text, d.val_text, d.proposal_status].filter(Boolean).join(" ").toLowerCase();
            } else {
                haystack = [d.agent, d.action, d.details, d.reason].filter(Boolean).join(" ").toLowerCase();
            }
            if (!haystack.includes(kw)) return false;
        }

        return true;
    });

    function pulseLabel(s) {
        return s === 0 ? "Next" : s === 1 ? "Active" : "Executed";
    }
    function pulseClass(s) {
        return s === 0 ? "next" : s === 2 ? "done" : "";
    }

    return (
        <div className="card">
            <div className="card-title" style={{ marginBottom: 8 }}>Full Timeline</div>

            {/* ── Filter bar ── */}
            <div className="timeline-filter-bar">
                <input
                    className="timeline-filter-input"
                    type="text"
                    placeholder="Search keyword…"
                    value={filterKeyword}
                    onChange={e => setFilterKeyword(e.target.value)}
                />
                <select
                    className="timeline-filter-select"
                    value={filterKind}
                    onChange={e => setFilterKind(e.target.value)}
                >
                    <option value="all">All types</option>
                    <option value="pulse">Pulse</option>
                    <option value="proposal">Proposal</option>
                    <option value="event">Agent event</option>
                </select>
                <select
                    className="timeline-filter-select"
                    value={filterMember}
                    onChange={e => setFilterMember(e.target.value)}
                >
                    <option value="">All members</option>
                    {memberNames.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                {(filterKind !== "all" || filterMember || filterKeyword) && (
                    <button
                        className="timeline-filter-clear"
                        onClick={() => { setFilterKind("all"); setFilterMember(""); setFilterKeyword(""); }}
                    >✕ Clear</button>
                )}
                <span className="timeline-filter-count">{entries.length} / {allEntries.length}</span>
            </div>

            <div className="timeline">
                {entries.length === 0 && <div className="empty-state">No activity yet</div>}
                {entries.map((entry, i) => {
                    if (entry.kind === "pulse") {
                        const p = entry.data;
                        return (
                            <div key={`pulse-${p.id}`} className={`timeline-item ${pulseClass(p.status)} clickable`}
                                 onClick={() => openDetail("pulse", p.id, `Pulse ${pulseLabel(p.status)}`)}>
                                <div className="timeline-marker pulse-marker">PULSE</div>
                                <div className="timeline-title">Pulse — {pulseLabel(p.status)}</div>
                                <div className="timeline-meta">
                                    Support: {p.support_count}/{p.threshold} · {formatTime(p.created_at)}
                                </div>
                            </div>
                        );
                    }
                    if (entry.kind === "proposal") {
                        const p = entry.data;
                        const statusClass = p.proposal_status === "Accepted" ? "status-accepted"
                                          : p.proposal_status === "Rejected" ? "status-rejected" : "";
                        return (
                            <div key={`prop-${p.id}`} className="timeline-item clickable"
                                 onClick={() => openDetail("proposal", p.id, p.proposal_type)}>
                                <div className="timeline-marker proposal-marker">PROP</div>
                                <div className="timeline-title">
                                    <span className={`mini-badge ${statusClass}`}>{p.proposal_status}</span>
                                    {" "}<span className="detail-type-badge">{p.proposal_type}</span>
                                </div>
                                <div className="timeline-meta">{truncate(proposalDisplayText(p), 80)}</div>
                                <div className="timeline-meta">Support: {p.support_count} · {formatTime(p.created_at)}</div>
                            </div>
                        );
                    }
                    // agent event
                    const ev = entry.data;
                    const agent = Object.values(agentsByUserId || {}).find(a => a.name === ev.agent);
                    return (
                        <div key={`ev-${i}`} className="timeline-item timeline-event">
                            <div className="timeline-event-row">
                                <span className="event-time">{formatTime(ev.time)}</span>
                                <span className={`event-agent ${agentColor(ev.agent)} entity-link`}
                                      onClick={() => agent && openDetail("user", agent.user_id, ev.agent)}>
                                    {ev.agent}
                                </span>
                                <span className={`event-badge badge-${ev.action}`}>{ev.action}</span>
                                <span className="timeline-event-detail">
                                    <LinkedDetails details={ev.details} refId={ev.ref_id} openDetail={openDetail} />
                                </span>
                            </div>
                            {ev.reason && <div className="event-reason" style={{ marginLeft: 8 }}>{ev.reason}</div>}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ── Pulses Tab ─────────────────────────────────────────
function PulsesTab({ pulses, communityId, openDetail }) {
    const [expandedPulseId, setExpandedPulseId] = useState(null);
    const [pulseProposals, setPulseProposals] = useState([]);
    const [loadingProposals, setLoadingProposals] = useState(false);

    const sorted = [...(pulses || [])].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    function handleExpandPulse(pulseId) {
        if (expandedPulseId === pulseId) {
            setExpandedPulseId(null);
            setPulseProposals([]);
            return;
        }
        setExpandedPulseId(pulseId);
        setLoadingProposals(true);
        API.get(`/communities/${communityId}/proposals?pulse_id=${pulseId}`)
            .then(props => setPulseProposals(props))
            .catch(() => setPulseProposals([]))
            .finally(() => setLoadingProposals(false));
    }

    function statusLabel(s) {
        return s === 0 ? "Next" : s === 1 ? "Active" : "Executed";
    }

    return (
        <div className="card">
            <div className="card-title">Pulse History ({sorted.length})</div>
            {sorted.length === 0 && <div className="empty-state">No pulses yet</div>}
            {sorted.map((p, idx) => {
                const isExpanded = expandedPulseId === p.id;
                const label = statusLabel(p.status);
                const accepted = isExpanded ? pulseProposals.filter(pr => pr.proposal_status === "Accepted") : [];
                const rejected = isExpanded ? pulseProposals.filter(pr => pr.proposal_status === "Rejected") : [];
                const onTheAir = isExpanded ? pulseProposals.filter(pr => pr.proposal_status === "OnTheAir") : [];
                const isClickable = p.status === 2;
                const pulseNum = sorted.length - idx;

                return (
                    <div key={p.id} className="pulse-history-card">
                        <div
                            className={isClickable ? "clickable" : ""}
                            style={{
                                padding: "12px 16px", display: "flex",
                                justifyContent: "space-between", alignItems: "center",
                                background: isExpanded ? "var(--bg-card)" : "transparent",
                            }}
                            onClick={() => isClickable && handleExpandPulse(p.id)}
                        >
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                                <span style={{ fontWeight: 700, fontSize: "0.95rem" }}>
                                    Pulse #{pulseNum}
                                </span>
                                <span className={`mini-badge ${p.status === 2 ? "status-accepted" : p.status === 1 ? "status-outthere" : ""}`}>
                                    {label}
                                </span>
                            </div>
                            <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", display: "flex", gap: 12 }}>
                                <span>Support: {p.support_count}/{p.threshold}</span>
                                <span>{formatDate(p.created_at)}</span>
                                {isClickable && <span>{isExpanded ? "▾" : "▸"}</span>}
                            </div>
                        </div>
                        {isExpanded && (
                            <div style={{ padding: "8px 16px 16px", borderTop: "1px solid var(--border)" }}>
                                {loadingProposals && <div><span className="spinner"></span> Loading proposals...</div>}
                                {!loadingProposals && accepted.length === 0 && rejected.length === 0 && onTheAir.length === 0 && (
                                    <div className="empty-state">No proposals linked to this pulse</div>
                                )}
                                {accepted.length > 0 && (
                                    <div style={{ marginBottom: 8 }}>
                                        <div className="results-sub accepted">Accepted ({accepted.length})</div>
                                        {accepted.map(pr => (
                                            <div key={pr.id} className="pulse-proposal-item accepted clickable"
                                                 onClick={() => openDetail("proposal", pr.id, pr.proposal_type)}>
                                                <span className="detail-type-badge">{pr.proposal_type}</span>
                                                <span style={{ marginLeft: 8 }}>{truncate(proposalDisplayText(pr), 80)}</span>
                                                <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                                                    Support: {pr.support_count}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                                {rejected.length > 0 && (
                                    <div style={{ marginBottom: 8 }}>
                                        <div className="results-sub rejected">Rejected ({rejected.length})</div>
                                        {rejected.map(pr => (
                                            <div key={pr.id} className="pulse-proposal-item rejected clickable"
                                                 onClick={() => openDetail("proposal", pr.id, pr.proposal_type)}>
                                                <span className="detail-type-badge">{pr.proposal_type}</span>
                                                <span style={{ marginLeft: 8 }}>{truncate(proposalDisplayText(pr), 80)}</span>
                                                <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                                                    Support: {pr.support_count}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                                {onTheAir.length > 0 && (
                                    <div>
                                        <div className="results-sub" style={{ color: "var(--warning)" }}>On The Air ({onTheAir.length})</div>
                                        {onTheAir.map(pr => (
                                            <div key={pr.id} className="pulse-proposal-item clickable"
                                                 onClick={() => openDetail("proposal", pr.id, pr.proposal_type)}>
                                                <span className="detail-type-badge">{pr.proposal_type}</span>
                                                <span style={{ marginLeft: 8 }}>{truncate(proposalDisplayText(pr), 80)}</span>
                                                <span style={{ marginLeft: "auto", fontSize: "0.75rem", color: "var(--text-muted)" }}>
                                                    Support: {pr.support_count}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}

// ── News Ticker ─────────────────────────────────────────
// Global news queue that WebSocket events feed into
const _newsQueue = [];
const _newsListeners = new Set();
const MAX_NEWS = 40;

function addNewsItem(event) {
    const item = formatNewsEvent(event);
    if (!item) return;
    _newsQueue.unshift({ id: Date.now() + Math.random(), text: item, time: new Date(), type: event.event_type });
    if (_newsQueue.length > MAX_NEWS) _newsQueue.length = MAX_NEWS;
    _newsListeners.forEach(fn => fn([..._newsQueue]));
}

function formatNewsEvent(event) {
    const d = event.data || {};
    const agentName = d.agent_name || d.user_name || null;
    const communityName = d.community_name || null;
    const prefix = communityName ? `[${communityName}] ` : '';

    switch (event.event_type) {
        case 'proposal.accepted': {
            const pType = d.proposal_type || 'proposal';
            return `${prefix}${pType} proposal accepted`;
        }
        case 'proposal.rejected': {
            const pType = d.proposal_type || 'proposal';
            return `${prefix}${pType} proposal rejected`;
        }
        case 'pulse.executed':
            return `${prefix}Pulse fired!`;
        case 'round.start':
            return `Round ${d.round || '?'} started`;
        case 'round.end':
            return `Round ${d.round || '?'} ended`;
        case 'agent.action': {
            const action = d.action_type || d.action || '';
            const name = agentName || 'Agent';
            // Skip noisy/uninteresting events
            if (action === 'do_nothing' || action === 'create_proposal') return null;
            if (action === 'support_proposal' || action === 'support_pulse') return null;
            if (action === 'send_chat') return `${prefix}${name} posted in chat`;
            if (action === 'comment') return `${prefix}${name} commented on a proposal`;
            if (action) return `${prefix}${name}: ${action}`;
            return null;
        }
        default:
            return null;
    }
}

function NewsTicker() {
    const [items, setItems] = useState([..._newsQueue]);
    const prevLenRef = useRef(items.length);
    const scrollRef = useRef(null);

    useEffect(() => {
        _newsListeners.add(setItems);
        return () => _newsListeners.delete(setItems);
    }, []);

    // After render, measure new items and animate the shift
    useEffect(() => {
        const el = scrollRef.current;
        if (!el || items.length === 0) { prevLenRef.current = items.length; return; }
        const newCount = items.length - prevLenRef.current;
        if (newCount > 0) {
            // Measure width of newly prepended items
            let shiftPx = 0;
            const children = el.children;
            const gap = parseFloat(getComputedStyle(el).gap) || 24;
            for (let i = 0; i < Math.min(newCount, children.length); i++) {
                shiftPx += children[i].offsetWidth + gap;
            }
            // Instantly jump right (so new items are off-screen left)
            el.style.transition = 'none';
            el.style.transform = `translateX(-${shiftPx}px)`;
            // Force reflow then animate back to 0
            void el.offsetWidth;
            el.style.transition = 'transform 0.6s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
            el.style.transform = 'translateX(0)';
        }
        prevLenRef.current = items.length;
    }, [items]);

    if (items.length === 0) {
        return React.createElement('div', { className: 'news-ticker' },
            React.createElement('span', { className: 'news-ticker-label' }, 'LIVE'),
            React.createElement('div', { className: 'news-ticker-track' },
                React.createElement('span', { className: 'news-ticker-item news-empty' }, 'Waiting for events...')
            )
        );
    }

    return React.createElement('div', { className: 'news-ticker' },
        React.createElement('span', { className: 'news-ticker-label' }, 'LIVE'),
        React.createElement('div', { className: 'news-ticker-track' },
            React.createElement('div', { className: 'news-ticker-scroll', ref: scrollRef },
                items.map((item, i) =>
                    React.createElement('span', {
                        key: item.id,
                        className: `news-ticker-item ${item.type === 'proposal.accepted' ? 'news-accept' : ''} ${item.type === 'proposal.rejected' ? 'news-reject' : ''} ${item.type === 'pulse.executed' ? 'news-pulse' : ''}`
                    }, item.text)
                )
            )
        )
    );
}

// ── Main App ────────────────────────────────────────────
function App() {
    const [activeTab, setActiveTab] = useState("dashboard");
    const [status, setStatus] = useState(null);
    const [agents, setAgents] = useState([]);
    const [events, setEvents] = useState([]);
    const [proposals, setProposals] = useState([]);
    const [pulses, setPulses] = useState([]);
    const [runningRound, setRunningRound] = useState(false);
    const [paused, setPaused] = useState(false);
    const [connected, setConnected] = useState(false);
    const wsRef = useRef(null);

    // Scoped community navigation (for action sub-communities)
    const [activeCommunityId, setActiveCommunityId] = useState(null);
    const [activeCommunityName, setActiveCommunityName] = useState(null);
    const [activeCommunity, setActiveCommunity] = useState(null);  // full community object for the active action
    const [sidebarOpen, setSidebarOpen] = useState(false);
    const activeCommunityIdRef = useRef(null);

    // Detail panel navigation stack
    const [detailStack, setDetailStack] = useState([]);
    function openDetail(type, id, label) {
        setDetailStack(prev => [...prev, { type, id, label: label || `${type}` }]);
    }
    function popToIndex(i) {
        setDetailStack(prev => prev.slice(0, i + 1));
    }
    function closeDetail() {
        setDetailStack([]);
    }

    // Agent lookup by user_id — includes AI agents AND newcomer applicants
    const agentsByUserId = useMemo(() => {
        const map = {};
        (agents || []).forEach(a => { if (a.user_id) map[a.user_id] = a; });
        // Include newcomers (non-agent members) so their names resolve
        (status?.newcomers || []).forEach(n => {
            if (n.id && !map[n.id]) map[n.id] = { name: n.name, user_id: n.id };
        });
        return map;
    }, [agents, status]);

    const rootCommunityId = status?.community?.id;
    const bbUserId = status?.bb_user_id || null;
    const effectiveCommunityId = activeCommunityId || rootCommunityId;
    // For backward compat, keep communityId pointing to effective
    const communityId = effectiveCommunityId;

    // Keep ref in sync so fetchData always reads current value without recreating
    useEffect(() => { activeCommunityIdRef.current = activeCommunityId; }, [activeCommunityId]);

    // Fetch all data — uses ref so it never needs to be recreated on community change
    const fetchData = useCallback(async () => {
        try {
            const [statusData, agentsData, eventsData] = await Promise.all([
                API.get("/simulation/status"),
                API.get("/simulation/agents"),
                API.get("/simulation/events?limit=200"),
            ]);
            setStatus(statusData);
            setAgents(agentsData);
            setEvents(eventsData.events || []);
            if (statusData?.paused !== undefined) setPaused(statusData.paused);

            // Fetch proposals/pulses for the effective community (could be root or action)
            const cid = activeCommunityIdRef.current || statusData?.community?.id;
            if (cid) {
                const [proposalsData, pulsesData] = await Promise.all([
                    API.get(`/communities/${cid}/proposals`),
                    API.get(`/communities/${cid}/pulses`),
                ]);
                setProposals(proposalsData);
                setPulses(pulsesData);
            }
        } catch (err) {
            console.log("Waiting for simulation...", err.message);
        }
    }, []);  // stable — never recreated

    // Re-fetch proposals/pulses immediately when community changes
    useEffect(() => {
        fetchData();
    }, [activeCommunityId]);

    // WebSocket connection
    useEffect(() => {
        function connectWS() {
            const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
            const ws = new WebSocket(`${proto}//${window.location.host}${BASE}/ws/events`);

            ws.onopen = () => {
                setConnected(true);
                console.log("WebSocket connected");
            };

            ws.onmessage = (e) => {
                try {
                    const event = JSON.parse(e.data);
                    // Refresh on round boundaries and pulse execution
                    if (event.event_type === "round.end" || event.event_type === "pulse.executed") {
                        // Clear API cache so fresh data is fetched
                        Object.keys(_cache).forEach(k => delete _cache[k]);
                        fetchData();
                    }
                    // Also refresh when proposals are accepted/rejected (new artifacts, actions, etc.)
                    if (event.event_type === "proposal.accepted" || event.event_type === "proposal.rejected") {
                        Object.keys(_cache).forEach(k => delete _cache[k]);
                        fetchData();
                    }
                    // Feed the news ticker
                    if (typeof addNewsItem === 'function') {
                        addNewsItem(event);
                    }
                } catch (err) {
                    console.warn("WS parse error:", err);
                }
            };

            ws.onclose = () => {
                setConnected(false);
                console.log("WebSocket disconnected, reconnecting in 3s...");
                setTimeout(connectWS, 3000);
            };

            ws.onerror = () => ws.close();
            wsRef.current = ws;
        }

        connectWS();
        return () => {
            if (wsRef.current) wsRef.current.close();
        };
    }, [fetchData]);

    // Fetch action community details when activeCommunityId changes
    useEffect(() => {
        if (!activeCommunityId) { setActiveCommunity(null); return; }
        API.get(`/communities/${activeCommunityId}`)
            .then(setActiveCommunity)
            .catch(() => setActiveCommunity(null));
    }, [activeCommunityId]);

    // Initial data fetch + polling fallback
    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 5000);
        return () => clearInterval(interval);
    }, [fetchData]);

    // Pause / Resume
    async function handleTogglePause() {
        try {
            if (paused) {
                await API.post("/simulation/resume", {});
                setPaused(false);
            } else {
                await API.post("/simulation/pause", {});
                setPaused(true);
            }
        } catch (err) {
            console.error("Pause/resume error:", err);
        }
    }

    // Restart simulation loop (data preserved)
    const [restarting, setRestarting] = useState(false);
    async function handleRestart() {
        setRestarting(true);
        try {
            await API.post("/simulation/restart", {});
            let attempts = 0;
            const poll = setInterval(async () => {
                attempts++;
                try {
                    const s = await API.get("/simulation/status");
                    if (!s.restarting) {
                        clearInterval(poll);
                        setRestarting(false);
                        setStatus(s);
                    }
                } catch {}
                if (attempts > 30) { clearInterval(poll); setRestarting(false); }
            }, 500);
        } catch (err) {
            alert(`Restart failed: ${err.message}`);
            setRestarting(false);
        }
    }

    // Run round manually
    async function handleRunRound() {
        setRunningRound(true);
        try {
            await API.post("/simulation/run-round", {});
            await fetchData();
        } catch (err) {
            console.error("Run round error:", err);
        } finally {
            setRunningRound(false);
        }
    }

    // Navigate to an action's dashboard (or back to root)
    function handleNavigateToAction(actionId, actionName) {
        setActiveCommunityId(actionId);  // null = root
        setActiveCommunityName(actionName || null);
        setActiveTab("dashboard");
        // Clear cached proposals/pulses so they refresh for new community
        setProposals([]);
        setPulses([]);
    }

    function handleBackToRoot() {
        handleNavigateToAction(null, null);
    }

    return (
        <div>
            <Header
                status={status}
                openDetail={openDetail}
                activeCommunityId={activeCommunityId}
                activeCommunityName={activeCommunityName}
                onBackToRoot={handleBackToRoot}
                onToggleSidebar={() => setSidebarOpen(prev => !prev)}
            />
            <NewsTicker />
            <ActionBreadcrumb
                activeCommunityId={activeCommunityId}
                rootCommunityId={rootCommunityId}
                rootCommunityName={status?.community?.name}
                onNavigate={handleNavigateToAction}
            />
            <TabNav activeTab={activeTab} setActiveTab={setActiveTab} />
            <div className="app-body">
                <ActionSidebar
                    rootCommunityId={rootCommunityId}
                    activeCommunityId={activeCommunityId}
                    onNavigate={handleNavigateToAction}
                    openDetail={openDetail}
                    round={status?.round}
                    isOpen={sidebarOpen}
                    onClose={() => setSidebarOpen(false)}
                />
                {/* key=communityId: unmount/remount all tabs when navigating to a
                    different community, giving each community its own fresh state.
                    Within a community, tabs are always mounted — just hidden with
                    display:none — so their internal state (filters, draft text,
                    interview history) survives tab switches. */}
                <div className="main-content" key={communityId || "root"}>
                    <div style={{ display: activeTab === "dashboard" ? undefined : "none" }}>
                        <DashboardTab
                            status={status}
                            events={events}
                            proposals={proposals}
                            pulses={pulses}
                            onRunRound={handleRunRound}
                            runningRound={runningRound}
                            paused={paused}
                            onTogglePause={handleTogglePause}
                            onRestart={handleRestart}
                            restarting={restarting}
                            openDetail={openDetail}
                            agentsByUserId={agentsByUserId}
                            communityId={communityId}
                            activeCommunity={activeCommunity}
                            activeCommunityId={activeCommunityId}
                            rootCommunityId={rootCommunityId}
                        />
                    </div>
                    <div style={{ display: activeTab === "agents" ? undefined : "none" }}>
                        <AgentsTab agents={agents} openDetail={openDetail} communityId={communityId} rootCommunityId={rootCommunityId} />
                    </div>
                    <div style={{ display: activeTab === "variables" ? undefined : "none" }}>
                        <VariablesTab communityId={communityId} openDetail={openDetail} />
                    </div>
                    <div style={{ display: activeTab === "statements" ? undefined : "none" }}>
                        <StatementsTab communityId={communityId} openDetail={openDetail} />
                    </div>
                    <div style={{ display: activeTab === "pulses" ? undefined : "none" }}>
                        <PulsesTab pulses={pulses} communityId={communityId} openDetail={openDetail} />
                    </div>
                    <div style={{ display: activeTab === "actions" ? undefined : "none" }}>
                        <ActionTreeTab
                            communityId={communityId}
                            rootCommunityId={rootCommunityId}
                            openDetail={openDetail}
                            onNavigate={handleNavigateToAction}
                        />
                    </div>
                    <div style={{ display: activeTab === "work" ? undefined : "none" }}>
                        <WorkTab communityId={communityId} openDetail={openDetail} bbUserId={bbUserId} />
                    </div>
                    <div style={{ display: activeTab === "chat" ? undefined : "none" }}>
                        <ChatTab communityId={communityId} communityName={activeCommunityName || status?.community?.name || null} agentsByUserId={agentsByUserId} bbUserId={bbUserId} />
                    </div>
                    <div style={{ display: activeTab === "interview" ? undefined : "none" }}>
                        <InterviewTab agents={agents} />
                    </div>
                    <div style={{ display: activeTab === "timeline" ? undefined : "none" }}>
                        <TimelineTab pulses={pulses} proposals={proposals} events={events} openDetail={openDetail} agentsByUserId={agentsByUserId} activeCommunityId={activeCommunityId} rootCommunityId={rootCommunityId} />
                    </div>
                    <div style={{ display: activeTab === "relationships" ? undefined : "none" }}>
                        <RelationshipsTab communityId={communityId} agentsByUserId={agentsByUserId} openDetail={openDetail} />
                    </div>
                </div>
            </div>
            <DetailPanel
                stack={detailStack}
                popToIndex={popToIndex}
                closeDetail={closeDetail}
                openDetail={openDetail}
                agents={agents}
                agentsByUserId={agentsByUserId}
                communityId={communityId}
                events={events}
                bbUserId={bbUserId}
            />
        </div>
    );
}

// Mount
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
