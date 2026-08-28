from cryptography.fernet import Fernet

from app.store import StateStore


def test_delete_removes_encrypted_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(str(path), Fernet.generate_key().decode())
    store.save({"refresh_token": "secret"})

    store.delete()

    assert not path.exists()
    assert store.load() == {}
