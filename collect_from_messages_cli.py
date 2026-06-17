#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import FloodWait, PeerIdInvalid, UsernameInvalid, UsernameNotOccupied

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
    try:
        if value.startswith(("https://", "http://", "t.me/", "@")):
            return await client.get_chat(value)
        return await client.get_chat(int(value))
    except (ValueError, UsernameInvalid, UsernameNotOccupied, PeerIdInvalid) as exc:
        raise ValueError(f"Не удалось найти чат: {value}") from exc


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

    print("\n=== СБОР АВТОРОВ ИЗ СООБЩЕНИЙ (CLI) ===")
    print("Этот режим собирает пользователей, которые писали в чате.")
    session_name = choose_session()
    source_link = ask("Введи ссылку/username/ID группы или канала")
    max_messages = int(ask("Сколько сообщений просмотреть (0 = без лимита)", "5000"))
    interval = float(ask("Интервал между запросами (сек)", str(DEFAULT_COLLECT_INTERVAL)).replace(",", "."))
    output_name = ask("Имя TXT-файла базы", "writers.txt")

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
        print("Начинаю сбор авторов из истории...")

        rows: list[UserRow] = []
        scanned = 0

        async for msg in client.get_chat_history(chat.id):
            scanned += 1
            user = msg.from_user
            if user and not user.is_bot and not user.is_deleted:
                rows.append(
                    UserRow(
                        user_id=user.id,
                        username=user.username,
                        first_name=user.first_name,
                        last_name=user.last_name,
                    )
                )

            if scanned % 100 == 0:
                print(f"  сообщений просмотрено: {scanned}")

            if max_messages > 0 and scanned >= max_messages:
                break

            try:
                await asyncio.sleep(interval)
            except FloodWait as exc:
                await asyncio.sleep(exc.value)

        rows = unique_users(rows)
        if not rows:
            print("\nНе найдено пользователей среди авторов сообщений.")
            print("Проверь, есть ли публичная история и права на чтение сообщений.")
            return

        out_path.write_text("\n".join(item.to_txt_line() for item in rows), encoding="utf-8")
        print(f"\nГотово. Уникальных авторов: {len(rows)}")
        print(f"Просмотрено сообщений: {scanned}")
        print(f"База сохранена: {out_path}")
    finally:
        await client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
