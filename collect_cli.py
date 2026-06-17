#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import ChatAdminRequired, FloodWait, PeerIdInvalid, UsernameInvalid, UsernameNotOccupied

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSIONS_DIR = ROOT / os.getenv("SESSIONS_DIR", "sessions")
DATA_DIR = ROOT / os.getenv("DATA_DIR", "data")
DEFAULT_COLLECT_INTERVAL = float(os.getenv("DEFAULT_COLLECT_INTERVAL", "1.0"))


@dataclass
class UserRow:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None

    def to_txt_line(self) -> str:
        username = f"@{self.username}" if self.username else "-"
        full_name = " ".join(part for part in [self.first_name, self.last_name] if part).strip() or "-"
        return f"{self.user_id} | {username} | {full_name}"


def list_sessions() -> list[str]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path.stem for path in SESSIONS_DIR.glob("*.session") if path.is_file())


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def choose_session() -> str:
    sessions = list_sessions()
    if not sessions:
        raise RuntimeError(
            "Не найдены session-файлы в папке sessions/. "
            "Создай сессию через: python create_session.py session1"
        )
    print("\nДоступные сессии:")
    for idx, name in enumerate(sessions, start=1):
        print(f"  {idx}. {name}")
    raw = ask("Выбери номер сессии", "1")
    idx = int(raw) - 1
    if idx < 0 or idx >= len(sessions):
        raise ValueError("Неверный номер сессии")
    return sessions[idx]


async def resolve_chat(client: Client, raw: str):
    value = raw.strip()
    if not value:
        raise ValueError("Ссылка/ID пустая")

    normalized = normalize_chat_reference(value)
    try:
        if isinstance(normalized, int):
            return await client.get_chat(normalized)
        return await client.get_chat(normalized)
    except (ValueError, UsernameInvalid, UsernameNotOccupied, PeerIdInvalid) as exc:
        raise ValueError(f"Не удалось найти чат: {value}") from exc


def normalize_chat_reference(raw: str) -> int | str:
    value = raw.strip()
    if not value:
        raise ValueError("Ссылка/ID пустая")

    # Numeric chat id: -100..., -..., 123...
    if re.fullmatch(r"-?\d+", value):
        return int(value)

    if value.startswith("@"):
        username = value[1:].strip()
        if not username:
            raise ValueError("Пустой username после @")
        return username

    lower = value.lower()
    if lower.startswith(("https://", "http://")):
        parsed = urlparse(value)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")

        if host.endswith("t.me") or host.endswith("telegram.me"):
            if not path:
                raise ValueError("В ссылке t.me не указан чат")

            first_part = path.split("/", 1)[0].strip()
            if first_part in {"c", "joinchat", "s", "share", "addstickers"}:
                raise ValueError(
                    "Этот тип ссылки не подходит для сбора. "
                    "Используй публичный @username или ссылку вида https://t.me/<username>."
                )
            if first_part.startswith("+"):
                raise ValueError(
                    "Invite-ссылка требует вступления. Для открытого чата укажи @username."
                )
            return first_part

        # Any other URL is not a Telegram chat reference.
        raise ValueError("Поддерживаются только ссылки t.me/telegram.me")

    if lower.startswith("t.me/") or lower.startswith("telegram.me/"):
        _, _, path = value.partition("/")
        path = path.strip("/")
        if not path:
            raise ValueError("В ссылке t.me не указан чат")
        first_part = path.split("/", 1)[0].strip()
        if first_part.startswith("+") or first_part in {"joinchat", "c", "s"}:
            raise ValueError(
                "Эта ссылка не подходит. Используй публичный @username или https://t.me/<username>."
            )
        return first_part

    # Plain username without @
    return value


def unique_users(rows: Iterable[UserRow]) -> list[UserRow]:
    seen: set[int] = set()
    result: list[UserRow] = []
    for row in rows:
        if row.user_id in seen:
            continue
        seen.add(row.user_id)
        result.append(row)
    return result


async def run() -> None:
    if API_ID <= 0 or not API_HASH:
        raise RuntimeError("Заполни API_ID и API_HASH в sctipt/.env")

    print("\n=== СБОР УЧАСТНИКОВ (CLI) ===")
    session_name = choose_session()
    source_link = ask("Введи ссылку/username/ID группы или канала")
    interval = float(ask("Интервал между запросами (сек)", str(DEFAULT_COLLECT_INTERVAL)).replace(",", "."))
    output_name = ask("Имя TXT-файла базы", "members.txt")

    out_path = DATA_DIR / output_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    client = Client(
        name=str(SESSIONS_DIR / session_name),
        api_id=API_ID,
        api_hash=API_HASH,
        no_updates=True,
        sleep_threshold=60,
    )

    print("\nПодключение к Telegram...")
    await client.start()
    try:
        me = await client.get_me()
        print(f"Аккаунт: @{me.username}" if me.username else f"Аккаунт ID: {me.id}")

        chat = await resolve_chat(client, source_link)
        print(f"Источник: {chat.title} ({chat.id})")
        print("Начинаю сбор...")

        rows: list[UserRow] = []
        total_seen = 0
        try:
            async for member in client.get_chat_members(chat.id):
                user = member.user
                if user.is_bot or user.is_deleted:
                    continue

                total_seen += 1
                rows.append(
                    UserRow(
                        user_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        last_name=user.last_name,
                    )
                )
                if total_seen % 25 == 0:
                    print(f"  обработано: {total_seen}")

                await asyncio.sleep(interval)
        except ChatAdminRequired:
            print("\nСбор невозможен: список участников скрыт или нет прав администратора.")
            return
        except FloodWait as exc:
            print(f"\nFloodWait: нужно подождать {exc.value} сек.")
            return

        rows = unique_users(rows)
        out_path.write_text("\n".join(item.to_txt_line() for item in rows), encoding="utf-8")
        print(f"\nГотово. Уникальных пользователей: {len(rows)}")
        print(f"База сохранена: {out_path}")
    finally:
        await client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
