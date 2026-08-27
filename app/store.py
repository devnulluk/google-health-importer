import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


class StateStore:
    def __init__(self, path: str, encryption_key: str) -> None:
        self.path = Path(path)
        self.cipher = Fernet(encryption_key.encode())

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.cipher.decrypt(self.path.read_bytes()))

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(self.cipher.encrypt(json.dumps(state).encode()))
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
