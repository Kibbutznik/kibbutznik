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
    # the JSON response. In prod this should be FALSE and an SMTP
    # integration should email the link instead.
    auth_dev_expose_magic_link: bool = True
    # Cookie name used for the session. Keep stable.
    auth_session_cookie: str = "kbz_session"

    # --- Finance module (opt-in; Phase 1 = internal credits) ---
    # One-time gift to every new human user on first sign-in. Makes
    # the escrow-on-apply membership-fee flow tractable without real
    # money. Set to 0 to disable.
    welcome_credits: str = "100"   # stringified Decimal, parsed by WalletService
    # HMAC secret for /webhooks/wallet-deposit. In prod MUST be
    # rotated to a high-entropy random. Empty string disables the
    # webhook endpoint entirely (returns 503).
    webhook_secret: str = ""
    # Which WalletBacking to instantiate when a community's
    # `Financial` variable is "internal". Phase 2+ will route to
    # "safe" / "stripe" based on the variable value itself.
    wallet_backing: str = "internal"

    model_config = {"env_prefix": "KBZ_"}


settings = Settings()
