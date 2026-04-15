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

    model_config = {"env_prefix": "KBZ_"}


settings = Settings()
