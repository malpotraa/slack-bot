"""Manual DB init script — `uv run python -m scripts.init_db`."""

from __future__ import annotations

import asyncio

from app.db.engine import init_db
from app.logging_setup import configure_logging


async def amain() -> None:
    configure_logging()
    await init_db()


if __name__ == "__main__":
    asyncio.run(amain())
