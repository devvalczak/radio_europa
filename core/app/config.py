from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JARVIS_", env_file=None)

    database_url: str = "sqlite:////data/jarvis.db"
    version: str = ""  # shown in Diagnostyka; set JARVIS_VERSION at build time (e.g. git sha)
    secret_key: str = ""
    setup_token: str = ""
    cookie_secure: bool = True
    session_hours: int = 24 * 14
    modules_dir: str = "/srv/modules"
    scheduler_interval: int = 30

    # Logging: readable text on stdout + JSON lines in a rotating file (empty log_dir = stdout only)
    log_level: str = "INFO"
    log_dir: str = "/data/logs"
    log_max_mb: float = 10
    log_backups: int = 5
    # audit log and login history older than this are deleted by a daily cleanup
    retention_days: int = 90

    # Database backups (core/app/backup.py). Empty backup_dir = off. mirror_dir: optional second copy (external disk,
    # NAS, a folder synced to the cloud). The encryption key is never in a backup: keep .env elsewhere.
    backup_dir: str = "/data/backups"
    backup_every_hours: float = 24
    backup_keep_daily: int = 7
    backup_keep_weekly: int = 4
    backup_mirror_dir: str = ""

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
