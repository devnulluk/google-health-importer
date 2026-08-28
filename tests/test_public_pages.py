from cryptography.fernet import Fernet

from app.config import get_settings
from app.main import homepage, privacy


def test_public_pages_disclose_purpose_and_contact(monkeypatch) -> None:
    values = {
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "https://importer.example/oauth/callback",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "OPEN_WEARABLES_URL": "https://wearables.example",
        "OPEN_WEARABLES_USER_ID": "user",
        "OPEN_WEARABLES_API_KEY": "key",
        "APP_SESSION_SECRET": "admin-secret",
        "PUBLIC_CONTACT_EMAIL": "privacy+test@example.com",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    assert "self-hosted, read-only bridge" in homepage()
    policy = privacy()
    assert "privacy+test@example.com" in policy
    assert "Limited Use requirements" in policy
    assert "POST /disconnect" in policy

    get_settings.cache_clear()
