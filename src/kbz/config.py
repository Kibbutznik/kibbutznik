from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://uriee@localhost:5432/kbz"
    test_database_url: str = "postgresql+asyncpg://uriee@localhost:5432/kbz_test"
    secret_key: str = "dev-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- Temporal Knowledge Graph ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"
    tkg_embed_dim: int = 768
    tkg_dual_write: bool = True
    tkg_semantic_timeout_ms: int = 300
    tkg_base_url: str = "http://localhost:8000"

    # --- Email (transactional) ---
    # Backend: "log" (dev/test — no network) or "resend" (real sends).
    # If "resend" is chosen but resend_api_key is empty we degrade to log.
    email_backend: str = "log"
    resend_api_key: str = ""
    # Must be an address on a DOMAIN you've verified in Resend. For dev
    # we default to the reserved `onboarding@resend.dev` which works out
    # of the box for the first 50 test sends per API key.
    # Production: kibbutznik.org is verified as an apex domain in Resend,
    # so set KBZ_EMAIL_FROM="Kibbutznik <hello@kibbutznik.org>" (or
    # noreply@kibbutznik.org if you don't want to monitor replies).
    email_from: str = "Kibbutznik <onboarding@resend.dev>"
    # Public origin used to build absolute URLs in OUTBOUND email bodies
    # (magic links, invite links). Email clients cannot resolve relative
    # hrefs — they see `http:///auth/verify?…` and bail. Set to e.g.
    # "https://kibbutznik.org/kbz" in prod. Empty means the email body
    # falls back to a relative URL (only useful for manual inspection
    # in dev; never click it from an inbox).
    public_base_url: str = ""

    # --- Human auth (Track C) ---
    # Session lifetime — cookie + DB token — deliberately short so a
    # leaked cookie has limited blast radius. Magic links are even
    # shorter (15 min).
    # Two TTLs: the long one fires when the user ticks "remember me" on
    # the login form; the short one for shared-device sign-ins.
    auth_session_ttl_minutes: int = 60 * 24 * 30      # 30 days (remember me)
    auth_session_short_ttl_minutes: int = 60 * 24 * 1 # 1 day (shared device)
    auth_magic_link_ttl_minutes: int = 15
    auth_invite_ttl_hours: int = 72
    # In dev mode, /auth/request-magic-link returns the verify URL in
    # the JSON response. Defaults to FALSE so the link is NEVER exposed
    # over the wire unless an operator explicitly opts in for local dev
    # (KBZ_AUTH_DEV_EXPOSE_MAGIC_LINK=true). A True default was an
    # account-takeover footgun: any redeploy/self-host that forgot to
    # override it would hand the live login token back in the HTTP
    # response to anyone who requested a link for any address.
    auth_dev_expose_magic_link: bool = False
    # Cookie name used for the session. Keep stable.
    auth_session_cookie: str = "kbz_session"
    # Set Secure=True on the session cookie. Default True so accidental
    # plaintext deploys don't leak cookies; explicitly set
    # KBZ_AUTH_COOKIE_SECURE=false ONLY for localhost dev over HTTP.
    auth_cookie_secure: bool = True
    # Comma-separated list of trusted CORS origins. "*" disables CORS
    # entirely (no header sent) which is safe for an API-only deploy
    # but means browsers won't send cookies cross-origin. Set to e.g.
    # "https://kibbutznik.org" in prod.
    cors_allow_origins: str = ""
    # Trust X-Forwarded-For only when the immediate peer is on a trusted
    # proxy. Prod sits behind nginx on 127.0.0.1, so default to loopback
    # subnets. Anyone hitting FastAPI directly (or via a malicious
    # downstream) cannot spoof their IP.
    trusted_proxy_cidrs: str = "127.0.0.0/8,::1/128"

    # --- Finance module (opt-in; Phase 1 = internal credits) ---
    # One-time gift to every new human user on first sign-in. Makes
    # the escrow-on-apply membership-fee flow tractable without real
    # money. Set to 0 to disable.
    welcome_credits: str = "100"   # stringified Decimal, parsed by WalletService
    # HMAC secret for /webhooks/wallet-deposit. In prod MUST be
    # rotated to a high-entropy random. Empty string disables the
    # webhook endpoint entirely (returns 503).
    webhook_secret: str = ""
    # Per-event amount cap for the wallet-deposit webhook. Even with a
    # valid HMAC signature, a single event over this limit is rejected
    # — defense-in-depth in case the secret leaks. Default 1,000,000
    # internal credits; override with KBZ_WEBHOOK_MAX_AMOUNT for prod
    # rails that legitimately move more.
    webhook_max_amount: str = "1000000"
    # Comma-separated list of user_id UUIDs allowed to call ops-style
    # endpoints (TKG prune, future fleet-wide DELETEs). Empty list
    # disables those endpoints entirely (returns 403). Set to a small
    # list of trusted operator UUIDs in prod.
    admin_user_ids: str = ""
    # Which WalletBacking to instantiate when a community's
    # `Financial` variable is "internal". Phase 2+ will route to
    # "safe" / "stripe" based on the variable value itself.
    wallet_backing: str = "internal"

    # Where to email "Get in touch" contact-form submissions. Empty
    # (default) = DB-only: messages are still persisted and readable via
    # the admin-gated GET /admin/contact, just no email is sent. Set to
    # the operator's address (e.g. an inbox you watch) to also receive a
    # notification email per submission, with reply_to set to the
    # visitor so you can reply directly.
    contact_notify_email: str = ""

    # Shared secret that authorizes a COOKIELESS caller to act as the
    # user_id named in a request body (the simulation orchestrator does
    # this — one process acting on behalf of many bot users). Sent as the
    # `X-KBZ-Agent-Secret` header.
    #
    # Empty string (the default) = DISABLED, which preserves the legacy
    # permissive behavior: any cookieless caller is trusted. That keeps
    # local dev + the test suite working unchanged. In PROD set this to a
    # high-entropy random AND give the same value to the sim client
    # (KBZ_AGENT_API_SECRET in its env) — then any cookieless write that
    # does NOT carry the secret is rejected 401, closing the anonymous
    # impersonation hole. nginx should also be configured to strip any
    # client-supplied X-KBZ-Agent-Secret on the public path.
    agent_api_secret: str = ""

    model_config = {"env_prefix": "KBZ_"}


settings = Settings()
