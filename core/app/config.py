from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=None)

    database_url: str = "sqlite:////data/jarvis.db"
    secret_key: str = ""
    setup_token: str = ""
    cookie_secure: bool = True
    session_hours: int = 24 * 14
    modules_dir: str = "/srv/modules"
    scheduler_interval: int = 30

    # AI (optional). provider: "" (off) | "anthropic" | "openai" (any OpenAI-compatible API, e.g. Ollama at
    # http://host:11434/v1). The model has no default on purpose: set JARVIS_AI_MODEL explicitly.
    ai_provider: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    ai_base_url: str = ""

    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"


settings = Settings()
