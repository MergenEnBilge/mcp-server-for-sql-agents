"""Encryption for connection secrets stored in app_meta.connections.secret_encrypted."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    """Encrypts and decrypts short secrets (database passwords) with Fernet.

    Accepts several keys so keys can be rotated without downtime: the first key
    encrypts new values, any listed key can decrypt old ones.
    """

    def __init__(self, keys: str) -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            raise ValueError("at least one Fernet key is required")
        try:
            self._fernet = MultiFernet([Fernet(k) for k in parts])
        except ValueError as exc:
            raise ValueError(
                "connection secret key is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "could not decrypt a connection secret: wrong key, or the value is corrupted"
            ) from exc
