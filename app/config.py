from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_client_id: str
    google_client_secret: SecretStr
    google_redirect_uri: str
    token_encryption_key: SecretStr
    open_wearables_url: str
    open_wearables_user_id: str
    open_wearables_api_key: SecretStr
    app_session_secret: SecretStr
    public_contact_email: str
    app_admin_user: str = "mark"
    sync_interval_minutes: int = 15
    sync_batch_size: int = 1000
    state_path: str = "/data/state.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
