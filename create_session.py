#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSIONS_DIR = ROOT / os.getenv("SESSIONS_DIR", "sessions")


async def main() -> None:
    if API_ID <= 0 or not API_HASH:
        raise RuntimeError("Заполни API_ID и API_HASH в sctipt/.env")

    if len(sys.argv) < 2:
        print("Usage: python create_session.py <session_name>")
        print("Example: python create_session.py session1")
        sys.exit(1)

    session_name = sys.argv[1].removesuffix(".session")
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    client = Client(
        name=str(SESSIONS_DIR / session_name),
        api_id=API_ID,
        api_hash=API_HASH,
    )

    async with client:
        me = await client.get_me()
        who = f"@{me.username}" if me.username else (me.first_name or str(me.id))
        print(f"Session '{session_name}' created for {who}")


if __name__ == "__main__":
    asyncio.run(main())
