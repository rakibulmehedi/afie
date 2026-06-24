from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    qstash_current_signing_key: str
    qstash_next_signing_key: str
    database_url: str
    synthesis_model: str = "claude-haiku-4-5-20251001"
    qstash_distribution_topic: str = ""  # URL or topic for distribution worker; empty = dry-run
    qstash_token: str = ""  # needed to publish outbound QStash messages

    model_config = {"env_file": ".env", "case_sensitive": False}


@lru_cache
def get_settings() -> Settings:
    return Settings()
