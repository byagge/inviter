#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.errors import (
    ChatAdminRequired,
    ChatWriteForbidden,
    FloodWait,
    PeerFlood,
    UserAlreadyParticipant,
    UserBannedInChannel,
    UserChannelsTooMuch,
    UserIdInvalid,
    UserKicked,
    UserNotMutualContact,
    UserPrivacyRestricted,
)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
SESSIONS_DIR = ROOT / os.getenv("SESSIONS_DIR", "sessions")
DATA_DIR = ROOT / os.getenv("DATA_DIR", "data")
DEFAULT_INVITE_INTERVAL = float(os.getenv("DEFAULT_INVITE_INTERVAL", "30.0"))
DEFAULT_INVITES_PER_SESSION = int(os.getenv("DEFAULT_INVITES_PER_SESSION", "50"))


@dataclass
class SessionRuntime:
    name: str
    client: Client
    invited: int = 0


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def list_sessions() -> list[str]:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path.stem for path in SESSIONS_DIR.glob("*.session") if path.is_file())


def choose_sessions() -> list[str]:
    sessions = list_sessions()
    if not sessions:
        raise RuntimeError(
            "Не найдены session-файлы в папке sessions/. "
            "Создай сессию через: python create_session.py session1"
        )
    print("\nДоступные сессии:")
    for idx, name in enumerate(sessions, start=1):
        print(f"  {idx}. {name}")

    raw = ask("Выбери сессии через запятую (например 1,2,3)", "1")
    picked: list[str] = []
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        i = int(token) - 1
        if i < 0 or i >= len(sessions):
            raise ValueError(f"Неверный номер сессии: {token}")
        picked.append(sessions[i])

    seen: set[str] = set()
    result: list[str] = []
    for item in picked:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def parse_txt_base(path: Path) -> list[int]:
    if not path.exists():
        raise FileNotFoundError(f"Файл базы не найден: {path}")

    users: list[int] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if "|" in line:
            first = line.split("|", 1)[0].strip()
            if first.lstrip("-").isdigit():
                users.append(int(first))
            continue

        if line.lstrip("-").isdigit():
            users.append(int(line))

    seen: set[int] = set()
    uniq: list[int] = []
    for user_id in users:
        if user_id in seen:
            continue
        seen.add(user_id)
        uniq.append(user_id)
    return uniq


async def list_invitable_chats(client: Client) -> list[tuple[int, str]]:
    chats: list[tuple[int, str]] = []
    async for dialog in client.get_dialogs():
        chat = dialog.chat
        if chat.type not in {ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL}:
            continue
        title = chat.title or str(chat.id)
        chats.append((chat.id, title))
    return chats


async def choose_target_chat(runtime: SessionRuntime):
    client = runtime.client
    print("\nЗагружаю список чатов аккаунта...")
    chats = await list_invitable_chats(client)
    if chats:
        print("Чаты/каналы аккаунта:")
        for idx, (chat_id, title) in enumerate(chats[:80], start=1):
            print(f"  {idx}. {title} ({chat_id})")
    else:
        print("Список чатов пуст. Можно ввести ссылку/ID вручную.")

    raw = ask("Выбери номер из списка ИЛИ введи ссылку/@username/ID")
    if raw.isdigit():
        i = int(raw) - 1
        if i < 0 or i >= len(chats):
            raise ValueError("Неверный номер чата")
        return await client.get_chat(chats[i][0])
    return await client.get_chat(raw)


async def start_runtime(session_name: str) -> SessionRuntime:
    client = Client(
        name=str(SESSIONS_DIR / session_name),
        api_id=API_ID,
        api_hash=API_HASH,
        no_updates=True,
        sleep_threshold=60,
    )
    await client.start()
    return SessionRuntime(name=session_name, client=client)


async def run() -> None:
    if API_ID <= 0 or not API_HASH:
        raise RuntimeError("Заполни API_ID и API_HASH в sctipt/.env")

    print("\n=== ИНВАЙТИНГ (CLI) ===")
    picked_sessions = choose_sessions()
    interval = float(ask("Интервал между инвайтами (сек)", str(DEFAULT_INVITE_INTERVAL)).replace(",", "."))
    invites_per_session = int(ask("Лимит инвайтов на 1 сессию", str(DEFAULT_INVITES_PER_SESSION)))
    txt_file = ask("Путь к TXT-базе", str(DATA_DIR / "members.txt"))
    user_ids = parse_txt_base(Path(txt_file))
    if not user_ids:
        raise ValueError("В TXT базе нет user_id")

    runtimes: list[SessionRuntime] = []
    for name in picked_sessions:
        rt = await start_runtime(name)
        runtimes.append(rt)
        me = await rt.client.get_me()
        print(f"Сессия {name} подключена как @{me.username}" if me.username else f"Сессия {name} подключена")

    try:
        target_chat = await choose_target_chat(runtimes[0])
        print(f"\nЦель: {target_chat.title} ({target_chat.id})")
        print(f"Пользователей в базе: {len(user_ids)}")
        print("Старт инвайтинга...\n")

        session_idx = 0
        invited = 0
        skipped = 0
        failed = 0

        for user_id in user_ids:
            if session_idx >= len(runtimes):
                print("Все сессии исчерпаны. Завершение.")
                break

            rt = runtimes[session_idx]
            client = rt.client

            if rt.invited >= invites_per_session:
                print(f"[{rt.name}] достигнут лимит ({invites_per_session}), переключаю на следующую сессию")
                session_idx += 1
                continue

            try:
                await client.add_chat_members(target_chat.id, user_id)
                invited += 1
                rt.invited += 1
                if invited % 10 == 0:
                    print(f"  приглашено: {invited} | пропущено: {skipped} | ошибок: {failed}")
            except UserAlreadyParticipant:
                skipped += 1
            except (UserPrivacyRestricted, UserNotMutualContact):
                skipped += 1
            except (UserBannedInChannel, UserKicked, UserChannelsTooMuch, UserIdInvalid):
                failed += 1
            except FloodWait as exc:
                print(f"[{rt.name}] FloodWait {exc.value}s")
                await asyncio.sleep(exc.value)
            except (PeerFlood, ChatWriteForbidden):
                print(f"[{rt.name}] лимит/ограничение Telegram, переключаю сессию")
                session_idx += 1
                continue
            except ChatAdminRequired:
                print("Нет прав администратора для добавления участников в этот чат.")
                break
            except Exception as exc:
                failed += 1
                print(f"[{rt.name}] ошибка для user {user_id}: {exc}")

            await asyncio.sleep(interval)

        print("\nГотово.")
        print(f"Приглашено: {invited}")
        print(f"Пропущено: {skipped}")
        print(f"Ошибок: {failed}")
        for rt in runtimes:
            print(f"  {rt.name}: {rt.invited} инвайтов")
    finally:
        for rt in runtimes:
            if rt.client.is_connected:
                await rt.client.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
