import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.enums import ArtifactStatus, ContainerStatus
from kbz.models.action import Action
from kbz.models.artifact import Artifact
from kbz.models.artifact_container import ArtifactContainer
from kbz.schemas.artifact import (
    ArtifactContainerResponse,
    ArtifactResponse,
    ContainerWithArtifactsResponse,
)
from kbz.services.artifact_service import ArtifactService

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/containers/community/{community_id}", response_model=list[ContainerWithArtifactsResponse])
async def list_containers_for_community(
    community_id: uuid.UUID,
    include_history: int = 0,
    db: AsyncSession = Depends(get_db),
):
    svc = ArtifactService(db)
    containers = await svc.list_containers(community_id)
    out: list[ContainerWithArtifactsResponse] = []
    for c in containers:
        artifacts = await svc.list_artifacts(c.id, include_history=bool(include_history))
        out.append(
            ContainerWithArtifactsResponse(
                container=ArtifactContainerResponse.model_validate(c),
                artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
            )
        )
    return out


@router.get("/containers/{container_id}", response_model=ContainerWithArtifactsResponse)
async def get_container(
    container_id: uuid.UUID,
    include_history: int = 0,
    db: AsyncSession = Depends(get_db),
):
    svc = ArtifactService(db)
    container = await svc.get_container(container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Container not found")
    artifacts = await svc.list_artifacts(container.id, include_history=bool(include_history))
    return ContainerWithArtifactsResponse(
        container=ArtifactContainerResponse.model_validate(container),
        artifacts=[ArtifactResponse.model_validate(a) for a in artifacts],
    )


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Return the current state of a single artifact."""
    svc = ArtifactService(db)
    a = await svc.get_artifact(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {
        "id": str(a.id),
        "container_id": str(a.container_id),
        "community_id": str(a.community_id),
        "title": a.title,
        "content": a.content,
        "author_user_id": str(a.author_user_id) if a.author_user_id else None,
        "proposal_id": str(a.proposal_id) if a.proposal_id else None,
        "status": int(a.status),
        "is_plan": getattr(a, "is_plan", False),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ─── Share-page in-process cache ──────────────────────────────────
# The /artifacts/{id}/share endpoint performs ~5 DB queries (artifact,
# visibility-walk, community, author, history) and renders ~10KB of
# HTML per call. Uncached, an HN traffic spike on a popular share URL
# would hammer Postgres. Cache the rendered HTML (or the 404) per
# artifact_id with a short TTL — the artifact's content can change
# from EditArtifact proposals, so stale ≤60s is the right cost/risk.
# Matches the pattern in routers/highlights.py:_CACHE.
import time as _time
_SHARE_CACHE: dict[str, tuple[float, int, str]] = {}  # id → (expires_at, status, html)
_SHARE_CACHE_TTL_S = 60.0
_SHARE_CACHE_MAX = 256  # cap memory; LRU-ish via timestamp eviction


def _share_cache_get(key: str):
    entry = _SHARE_CACHE.get(key)
    if entry is None:
        return None
    expires_at, status, body = entry
    if _time.time() > expires_at:
        _SHARE_CACHE.pop(key, None)
        return None
    return status, body


def _share_cache_set(key: str, status: int, body: str):
    # Cheap eviction: when oversized, drop the oldest-expired half.
    if len(_SHARE_CACHE) >= _SHARE_CACHE_MAX:
        to_drop = sorted(_SHARE_CACHE.items(), key=lambda kv: kv[1][0])[: _SHARE_CACHE_MAX // 2]
        for k, _ in to_drop:
            _SHARE_CACHE.pop(k, None)
    _SHARE_CACHE[key] = (_time.time() + _SHARE_CACHE_TTL_S, status, body)


@router.get("/{artifact_id}/share")
async def get_artifact_share_page(artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Public, shareable artifact page — server-rendered HTML so
    OG/Twitter crawlers see the title and excerpt without running JS.

    Visibility-gated: if the artifact's community resolves (via
    parent-chain walk) to a `private` root community, this returns
    404 — the page never reveals private content. `unlisted` and
    `public` both render normally; `unlisted` is "secret URL" sharing.

    Cached per-artifact_id for 60s to survive HN-launch spikes
    (otherwise each render is ~5 DB queries).
    """
    from html import escape as _esc
    from fastapi.responses import HTMLResponse, Response

    cache_key = str(artifact_id)
    cached = _share_cache_get(cache_key)
    if cached is not None:
        status, body = cached
        if status == 404:
            return Response(status_code=404, content=body)
        return HTMLResponse(content=body, headers={"Cache-Control": "public, max-age=300"})

    svc = ArtifactService(db)
    artifact = await svc.get_artifact(artifact_id)
    if not artifact or int(artifact.status) != int(ArtifactStatus.ACTIVE):
        _share_cache_set(cache_key, 404, "Artifact not found")
        return Response(status_code=404, content="Artifact not found")

    # Visibility gate. Walk to root, check Visibility variable.
    from kbz.services.community_service import CommunityService
    csvc = CommunityService(db)
    visibility = await csvc.get_effective_visibility(artifact.community_id)
    if visibility == "private":
        _share_cache_set(cache_key, 404, "Artifact not found")
        return Response(status_code=404, content="Artifact not found")

    # Resolve community + author display + history for the page.
    from kbz.models.community import Community
    from kbz.models.user import User
    community = (
        await db.execute(select(Community).where(Community.id == artifact.community_id))
    ).scalar_one_or_none()
    community_name = community.name if community else "Unknown community"

    author_name = None
    if artifact.author_user_id:
        u = (
            await db.execute(select(User.user_name).where(User.id == artifact.author_user_id))
        ).scalar_one_or_none()
        author_name = u

    history = await svc.get_history(artifact_id)
    edit_count = len(history) if history else 0

    title = (artifact.title or "").strip() or "Untitled"
    content = artifact.content or ""
    # Excerpt for OG description: strip markdown markers, truncate.
    import re as _re
    excerpt = _re.sub(r"[#*_`>\[\]]", "", content).strip()
    excerpt = _re.sub(r"\s+", " ", excerpt)[:200]

    # Build the body sections. Content is treated as Markdown-lite:
    # paragraphs split on blank lines, headings detected. Keep the
    # rendering simple — no full markdown engine, just enough to
    # display readably.
    paragraphs_html = []
    for block in content.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("### "):
            paragraphs_html.append(f"<h3>{_esc(block[4:])}</h3>")
        elif block.startswith("## "):
            paragraphs_html.append(f"<h2>{_esc(block[3:])}</h2>")
        elif block.startswith("# "):
            paragraphs_html.append(f"<h2>{_esc(block[2:])}</h2>")
        elif block.startswith("- ") or block.startswith("* "):
            items = [
                f"<li>{_esc(line[2:].strip())}</li>"
                for line in block.split("\n")
                if line.strip().startswith(("- ", "* "))
            ]
            paragraphs_html.append("<ul>" + "".join(items) + "</ul>")
        else:
            # paragraph — preserve in-line line breaks as <br>
            paragraphs_html.append(
                "<p>" + _esc(block).replace("\n", "<br>") + "</p>"
            )
    body_html = "\n".join(paragraphs_html) or '<p style="color:var(--ink-muted)">(empty)</p>'

    plan_badge = (
        '<span class="badge badge-plan">📋 Plan artifact</span>'
        if getattr(artifact, "is_plan", False) else ""
    )
    unlisted_note = (
        '<div class="unlisted-note">🔗 This is an <strong>unlisted</strong> artifact — '
        'visible to anyone with this exact URL, not in any browse listing.</div>'
        if visibility == "unlisted" else ""
    )

    history_summary = ""
    if edit_count > 0:
        history_summary = (
            f'<div class="history-meta">'
            f'<strong>{edit_count}</strong> '
            f'community decision{"s" if edit_count != 1 else ""} shaped this artifact.'
            f'</div>'
        )

    by_line = (
        f' &middot; first written by {_esc(author_name)}' if author_name else ""
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(title)} — {_esc(community_name)} on Kibbutznik</title>
<meta name="description" content="{_esc(excerpt)}">
<meta name="theme-color" content="#fdf8f0">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">
<meta property="og:type" content="article">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(excerpt)}">
<meta property="og:site_name" content="Kibbutznik">
<meta property="og:url" content="https://kibbutznik.org/artifact/{artifact_id}">
<meta property="og:image" content="https://kibbutznik.org/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{_esc(title)}">
<meta name="twitter:description" content="{_esc(excerpt)}">
<meta name="twitter:image" content="https://kibbutznik.org/og-image.png">
<script async src="https://plausible.io/js/pa-YUj2NGAFEeDownEFrcPPq.js"></script>
<script>window.plausible=window.plausible||function(){{(plausible.q=plausible.q||[]).push(arguments)}};plausible.init=plausible.init||function(){{}};plausible.init();</script>
<style>
  *,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
  :root{{
    --bg:#fdf8f0;--bg-alt:#f4ead8;--card:#fff;
    --ink:#2a2520;--ink-soft:#5a5249;--ink-muted:#8a8076;
    --pulse:#e94560;--pulse-soft:#fdebee;
    --sage:#6fa885;--sage-soft:#e3f0e8;
    --gold:#d9a441;--gold-soft:#fdf3e0;
    --line:#e8dec9;
  }}
  html{{scroll-behavior:smooth;-webkit-font-smoothing:antialiased}}
  body{{background:var(--bg);color:var(--ink);font-family:'Inter',system-ui,-apple-system,sans-serif;line-height:1.7}}
  a{{color:var(--pulse);text-decoration:none}}
  a:hover{{color:var(--ink);text-decoration:underline;text-underline-offset:3px}}
  .container{{max-width:760px;margin:0 auto;padding:0 24px}}
  .container-wide{{max-width:1100px;margin:0 auto;padding:0 24px}}

  nav.topbar{{padding:24px 0;background:rgba(253,248,240,.85);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);position:sticky;top:0;z-index:50}}
  nav.topbar .container-wide{{display:flex;align-items:center;justify-content:space-between;gap:16px}}
  .brand{{display:flex;align-items:center;gap:12px;font-weight:700;font-size:1.18rem;color:var(--ink)}}
  .brand img{{width:44px;height:44px}}
  nav.topbar .links{{display:flex;gap:28px;align-items:center}}
  nav.topbar .links a{{color:var(--ink-soft);font-weight:500;font-size:.95rem}}
  nav.topbar .links a{{white-space:nowrap}}
  nav.topbar .links a:hover{{color:var(--pulse);text-decoration:none}}
  /* Tiered mobile chrome — same shape as welcome.html / guide.html
     so the brand stays consistent. */
  @media(max-width:760px){{
    nav.topbar{{padding:16px 0}}
    nav.topbar .links{{gap:14px}}
    nav.topbar .links a:not(.btn){{font-size:.88rem}}
  }}
  @media(max-width:540px){{
    nav.topbar .links a:not(.btn){{display:none}}
    .brand{{font-size:1.05rem;gap:8px}}
    .brand img{{width:36px;height:36px}}
  }}

  .btn{{display:inline-flex;align-items:center;gap:8px;padding:12px 22px;border-radius:100px;font-weight:600;font-size:.95rem;border:1px solid transparent;cursor:pointer;transition:transform .12s,box-shadow .15s,background .15s}}
  .btn-primary{{background:var(--pulse);color:#fff;box-shadow:0 6px 16px rgba(233,69,96,.18)}}
  .btn-primary:hover{{background:#d9304a;color:#fff;transform:translateY(-1px);text-decoration:none;box-shadow:0 10px 22px rgba(233,69,96,.25)}}
  .btn-soft{{background:#fff;color:var(--ink);border-color:var(--line)}}
  .btn-soft:hover{{background:var(--card);border-color:var(--ink-muted);color:var(--ink);text-decoration:none;transform:translateY(-1px)}}

  .article-eyebrow{{display:inline-block;padding:6px 14px;background:var(--sage-soft);color:var(--sage);border-radius:100px;font-size:.82rem;font-weight:600;margin-bottom:18px;letter-spacing:.02em}}
  .badge{{display:inline-block;padding:3px 10px;border-radius:100px;font-size:.78rem;font-weight:600;margin-left:8px;vertical-align:middle}}
  .badge-plan{{background:var(--gold-soft);color:var(--gold)}}

  header.article-header{{padding:60px 0 32px;text-align:center;position:relative}}
  header.article-header::before{{content:'';position:absolute;inset:0;background:radial-gradient(circle at 30% 30%,rgba(111,168,133,.08) 0%,transparent 50%),radial-gradient(circle at 70% 70%,rgba(233,69,96,.06) 0%,transparent 50%);pointer-events:none}}
  h1.title{{font-size:clamp(2rem,5vw,2.8rem);font-weight:800;line-height:1.15;letter-spacing:-.025em;color:var(--ink);max-width:680px;margin:0 auto 14px}}
  .meta{{font-size:.95rem;color:var(--ink-soft)}}
  .meta a{{color:var(--ink);font-weight:600}}

  .unlisted-note{{max-width:680px;margin:24px auto 0;padding:14px 18px;background:var(--gold-soft);color:var(--ink-soft);border-radius:14px;font-size:.92rem;text-align:center}}

  article.body{{padding:48px 0;font-size:1.08rem;color:var(--ink)}}
  article.body p{{margin:18px 0;color:var(--ink-soft)}}
  article.body h2{{font-size:1.6rem;font-weight:700;margin:36px 0 12px;letter-spacing:-.02em;color:var(--ink)}}
  article.body h3{{font-size:1.25rem;font-weight:700;margin:28px 0 8px;color:var(--ink)}}
  article.body ul{{margin:14px 0 14px 28px;color:var(--ink-soft)}}
  article.body li{{margin:6px 0}}

  .history-meta{{margin:48px auto;padding:24px;background:var(--bg-alt);border-radius:16px;text-align:center;font-size:1rem;color:var(--ink-soft)}}
  .history-meta strong{{color:var(--pulse);font-size:1.25rem}}

  .end-cta{{background:var(--ink);color:#fff;border-radius:28px;padding:48px 32px;text-align:center;margin:48px 0}}
  .end-cta h2{{color:#fff;margin:0 0 12px;font-size:1.6rem;font-weight:700}}
  .end-cta p{{color:rgba(255,255,255,.78);font-size:1rem;margin:0 0 24px}}
  .end-cta .btn-soft{{background:rgba(255,255,255,.1);color:#fff;border-color:rgba(255,255,255,.2)}}
  .end-cta .btn-soft:hover{{background:rgba(255,255,255,.18);color:#fff}}
  .cta-row{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}}

  footer{{padding:40px 0;border-top:1px solid var(--line);color:var(--ink-muted);font-size:.9rem}}

  /* Body content & long-string protection. The artifact content is
     user/bot-authored markdown — could include UUIDs, JSON, URLs.
     Without break-word, those crash the layout horizontally. */
  article.body p, article.body li, article.body h2, article.body h3,
  h1.title, .meta, .end-cta, .end-cta p, .history-meta {{
    overflow-wrap: break-word;
    word-break: break-word;
  }}
  h1.title {{ hyphens: auto; }}

  @media(max-width:640px){{
    .container {{ padding: 0 16px; }}
    .container-wide {{ padding: 0 16px; }}
    header.article-header {{ padding: 40px 0 24px; }}
    article.body {{ padding: 32px 0; font-size: 1rem; }}
    article.body h2 {{ font-size: 1.35rem; margin: 28px 0 10px; }}
    article.body h3 {{ font-size: 1.1rem; margin: 22px 0 6px; }}
    .end-cta {{ padding: 36px 20px; border-radius: 22px; margin: 32px 0; }}
    .end-cta h2 {{ font-size: 1.4rem; }}
    .history-meta {{ padding: 18px; margin: 36px auto; font-size: .95rem; }}
    .unlisted-note {{ margin: 20px 0 0; padding: 12px 14px; font-size: .88rem; }}
    .btn {{ padding: 10px 18px; font-size: .9rem; }}
    footer {{ padding: 28px 0; font-size: .82rem; }}
    footer .container-wide {{ flex-direction: column; align-items: flex-start; gap: 12px; }}
    footer .socials {{ gap: 14px; }}
  }}
  footer .container-wide{{display:flex;justify-content:space-between;flex-wrap:wrap;gap:20px}}
  footer a{{color:var(--ink-soft)}}
  footer .socials{{display:flex;gap:18px;flex-wrap:wrap}}
</style>
</head>
<body>

<nav class="topbar">
  <div class="container-wide">
    <a href="/welcome.html" class="brand"><img src="/logo.svg" alt="">Kibbutznik</a>
    <div class="links">
      <a href="/welcome.html">Home</a>
      <a href="/guide.html">Guide</a>
      <a href="/ecosystem.html">Ecosystem</a>
      <a href="/kbz/viewer/">See it live</a>
      <a href="/app/" class="btn btn-primary">Open the app</a>
    </div>
  </div>
</nav>

<header class="article-header">
  <div class="container">
    <div class="article-eyebrow">An artifact from {_esc(community_name)}</div>
    <h1 class="title">{_esc(title)}{plan_badge}</h1>
    <div class="meta">From the kibbutz <strong>{_esc(community_name)}</strong>{by_line}</div>
    {unlisted_note}
  </div>
</header>

<article class="body">
  <div class="container">
    {body_html}
    {history_summary}
  </div>
</article>

<div class="container">
  <div class="end-cta">
    <h2>This was decided together.</h2>
    <p>Every paragraph above was proposed, supported, and accepted by the community that wrote it. See the kibbutz that made it — or start one of your own.</p>
    <div class="cta-row">
      <a href="/kbz/viewer/" class="btn btn-primary">See this kibbutz live →</a>
      <a href="/app/" class="btn btn-soft">Start your own</a>
    </div>
  </div>
</div>

<footer>
  <div class="container-wide">
    <div>© Kibbutznik · open source · made by people who believe small groups can run themselves</div>
    <div class="socials">
      <a href="/welcome.html">Home</a>
      <a href="/guide.html">Guide</a>
      <a href="/ecosystem.html">Ecosystem</a>
      <a href="/app/">App</a>
      <a href="/kbz/viewer/">Live</a>
      <a href="https://github.com/Kibbutznik/kibbutznik">GitHub</a>
    </div>
  </div>
</footer>

</body>
</html>"""
    _share_cache_set(cache_key, 200, html_doc)
    return HTMLResponse(content=html_doc, headers={"Cache-Control": "public, max-age=300"})


@router.get("/{artifact_id}/history")
async def get_artifact_history(artifact_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Edit history reconstructed from accepted EditArtifact proposals.

    Returns oldest-first list of edit entries. Each entry is a dict:
    {proposal_id, title, content, author_user_id, accepted_at}.
    """
    svc = ArtifactService(db)
    artifact = await svc.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return await svc.get_history(artifact_id)


@router.get("/communities/{community_id}/work_tree")
async def get_work_tree(community_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Recursive view: containers in this community, each with its artifacts,
    each artifact's child containers (delegations into sub-Actions), and so on.
    """
    svc = ArtifactService(db)
    visited: set[uuid.UUID] = set()

    async def render_container(c: ArtifactContainer) -> dict:
        if c.id in visited:
            return {"id": str(c.id), "title": c.title, "cycle": True}
        visited.add(c.id)
        artifacts = await svc.list_artifacts(c.id, include_history=False)
        artifact_nodes = []
        for a in artifacts:
            # Look for child containers delegated from this artifact.
            res = await db.execute(
                select(ArtifactContainer).where(
                    ArtifactContainer.delegated_from_artifact_id == a.id
                )
            )
            child_containers = list(res.scalars().all())
            children = [await render_container(cc) for cc in child_containers]
            artifact_nodes.append(
                {
                    "id": str(a.id),
                    "title": a.title,
                    "content": a.content,
                    "author_user_id": str(a.author_user_id),
                    "proposal_id": str(a.proposal_id) if a.proposal_id else None,
                    "is_plan": getattr(a, 'is_plan', False),
                    "status": a.status,
                    "delegated_to": children,
                }
            )
        return {
            "id": str(c.id),
            "community_id": str(c.community_id),
            "title": c.title,
            "mission": c.mission,
            "status": c.status,
            "delegated_from_artifact_id": str(c.delegated_from_artifact_id)
            if c.delegated_from_artifact_id
            else None,
            "committed_content": c.committed_content,
            "artifacts": artifact_nodes,
        }

    containers = await svc.list_containers(community_id)
    return [await render_container(c) for c in containers]
