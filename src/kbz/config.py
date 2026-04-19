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
    email_from: str = "KBZ <onboarding@resend.dev>"

    # --- Human auth (Track C) ---
    # Session lifetime — cookie + DB token — deliberately short so a
    # leaked cookie has limited blast radius. Magic links are even
    # shorter (15 min).
    auth_session_ttl_minutes: int = 60 * 24 * 7       # 7 days
    auth_magic_link_ttl_minutes: int = 15
    auth_invite_ttl_hours: int = 72
    # In dev mode, /auth/request-magic-link returns the verify URL in
    # the JSON response. In prod this should be FALSE and an SMTP
    # integration should email the link instead.
    auth_dev_expose_magic_link: bool = True
    # Cookie name used for the session. Keep stable.
    auth_session_cookie: str = "kbz_session"

    model_config = {"env_prefix": "KBZ_"}


settings = Settings()
