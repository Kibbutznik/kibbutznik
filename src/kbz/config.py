from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://uriee@localhost:5432/kbz"
    test_database_url: str = "postgresql+asyncpg://uriee@localhost:5432/kbz_test"
    secret_key: str = "dev-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    model_config = {"env_prefix": "KBZ_"}


settings = Settings()
