"""Fernet wrapper for encrypting OAuth tokens at rest."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class TokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise RuntimeError(
                "TOKEN_ENCRYPTION_KEY is empty. Generate one:\n"
                "  python -c \"from cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())"'
            )
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Token decryption failed — wrong key?") from exc


_cipher: TokenCipher | None = None


def cipher() -> TokenCipher:
    global _cipher
    if _cipher is None:
        _cipher = TokenCipher(settings.token_encryption_key)
    return _cipher
