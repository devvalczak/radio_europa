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

    # Shopping module (EMMA online store, scraped; no official API)
    emma_base_url: str = "https://emmamarket.pl"
    emma_delivery_url: str = ""  # page that shows the nearest delivery slot; empty = emma_base_url

    # Fernet key(s) used to encrypt stored credentials (mail passwords). Comma-separated list = key rotation:
    # the first key encrypts, all keys decrypt. Generate with scripts/gen_secrets.py.
    encryption_key: str = ""
    # Mail servers on private/LAN addresses are refused unless this is true (SSRF guard).
    allow_private_hosts: bool = False

    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"


settings = Settings()
