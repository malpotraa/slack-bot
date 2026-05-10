"""Generate an APP_SECRET_KEY and TOKEN_ENCRYPTION_KEY pair for .env."""

from __future__ import annotations

import secrets

from cryptography.fernet import Fernet


def main() -> None:
    print("# Paste these into .env\n")
    print(f"APP_SECRET_KEY={secrets.token_urlsafe(48)}")
    print(f"TOKEN_ENCRYPTION_KEY={Fernet.generate_key().decode()}")


if __name__ == "__main__":
    main()
