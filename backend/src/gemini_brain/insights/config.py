"""Insight settings. Every variable carries the INSIGHT_ prefix, so it never collides with the AI's own settings
(the AI's BEDROCK_MODEL_ID is a different model). Read from the same backend .env as the rest of the service."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gemini_brain.config.settings import _ENV_FILE, settings as ai_settings


class InsightSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSIGHT_", env_file=_ENV_FILE, env_file_encoding="utf-8",
                                      extra="ignore")

    # Tokens are introspected live against this Accutax API (see auth.py). Empty: the AI's ACCUTAX_BASE_URL.
    accutax_base_url: str = ""
    auth_http_timeout_seconds: float = 5.0
    auth_cache_ttl_seconds: int = 300

    # The model the standalone engine ran with. Region, credentials and retries: the AI's Bedrock client.
    bedrock_model_id: str = "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_max_tokens: int = 2000

    cache_ttl_seconds: int = 900
    cache_max_entries: int = 1000

    rate_limit_per_org_per_hour: int = 20

    def model_post_init(self, __context) -> None:
        self.accutax_base_url = self.accutax_base_url or ai_settings.accutax_base_url


@lru_cache
def get_settings() -> InsightSettings:
    return InsightSettings()
