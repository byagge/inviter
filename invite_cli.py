#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.errors import (
    ChatAdminRequired,
    ChatWriteForbidden,
    FloodWait,
    InviteHashExpired,
    PeerIdInvalid,
    PeerFlood,
    UserAlreadyParticipant,
    UserBannedInChannel,
    UserChannelsTooMuch,
    UserIdInvalid,
    UserKicked,
    UserNotMutualContact,
    UserPrivacyRestricted,
    UsernameInvalid,
    UsernameNotOccupied,
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


@dataclass
class InviteTarget:
    user_id: int | None
    username: str | None

    @property
    def add_value(self) -> int | str:
        if self.username:
            return self.username
        if self.user_id is None:
            raise ValueError("У цели нет ни user_id, ни username")
        return self.user_id


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


def parse_txt_base(path: Path) -> list[InviteTarget]:
    if not path.exists():
        raise FileNotFoundError(f"Файл базы не найден: {path}")

    users: list[InviteTarget] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if "|" in line:
            parts = [part.strip() for part in line.split("|")]
            first = parts[0] if parts else ""
            second = parts[1] if len(parts) > 1 else ""
            user_id: int | None = int(first) if first.lstrip("-").isdigit() else None
            username: str | None = None
            if second.startswith("@") and len(second) > 1:
                username = second[1:].strip() or None
            elif second and second != "-" and second.replace("_", "").isalnum():
                username = second
            if user_id is not None or username:
                users.append(InviteTarget(user_id=user_id, username=username))
            continue

        if line.lstrip("-").isdigit():
            users.append(InviteTarget(user_id=int(line), username=None))
            continue

        if line.startswith("@") and len(line) > 1:
            users.append(InviteTarget(user_id=None, username=line[1:].strip()))
            continue

        if line.replace("_", "").isalnum():
            users.append(InviteTarget(user_id=None, username=line))

    seen_ids: set[int] = set()
    seen_usernames: set[str] = set()
    uniq: list[InviteTarget] = []
    for user in users:
        username_key = (user.username or "").lower()
        if username_key and username_key in seen_usernames:
            continue
        if user.user_id is not None and user.user_id in seen_ids:
            continue
        if username_key:
            seen_usernames.add(username_key)
        if user.user_id is not None:
            seen_ids.add(user.user_id)
        uniq.append(user)
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


def normalize_chat_reference(raw: str) -> int | str:
    value = raw.strip()
    if not value:
        raise ValueError("Ссылка/ID пустая")

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
        if not (host.endswith("t.me") or host.endswith("telegram.me")):
            raise ValueError("Поддерживаются только ссылки t.me/telegram.me")
        if not path:
            raise ValueError("В ссылке t.me не указан чат")

        parts = [p for p in path.split("/") if p]
        first = parts[0]
        if first.startswith("+"):
            return f"https://t.me/{first}"
        if first == "joinchat" and len(parts) > 1:
            return f"https://t.me/joinchat/{parts[1]}"
        if first in {"c", "s", "share", "addstickers"}:
            raise ValueError("Эта ссылка не подходит для выбора чата-цели")
        return first

    if lower.startswith("t.me/") or lower.startswith("telegram.me/"):
        normalized = f"https://{value}"
        return normalize_chat_reference(normalized)

    return value


async def resolve_target_chat(client: Client, raw: str):
    source = raw.strip()
    normalized = normalize_chat_reference(source)
    try:
        return await client.get_chat(normalized)
    except InviteHashExpired as exc:
        raise ValueError("Invite-ссылка недействительна (истекла или отозвана)") from exc
    except (ValueError, UsernameInvalid, UsernameNotOccupied, PeerIdInvalid) as exc:
        raise ValueError(f"Не удалось открыть чат: {source}") from exc


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
    return await resolve_target_chat(client, raw)


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
    users = parse_txt_base(Path(txt_file))
    if not users:
        raise ValueError("В TXT базе нет user_id/username")

    runtimes: list[SessionRuntime] = []
    for name in picked_sessions:
        rt = await start_runtime(name)
        runtimes.append(rt)
        me = await rt.client.get_me()
        print(f"Сессия {name} подключена как @{me.username}" if me.username else f"Сессия {name} подключена")

    try:
        target_chat = await choose_target_chat(runtimes[0])
        print(f"\nЦель: {target_chat.title} ({target_chat.id})")
        print(f"Пользователей в базе: {len(users)}")
        print("Старт инвайтинга...\n")

        session_idx = 0
        invited = 0
        skipped = 0
        failed = 0

        for user in users:
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
                await client.add_chat_members(target_chat.id, user.add_value)
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
            except PeerIdInvalid:
                # Для "голого" user_id peer может быть неизвестен сессии.
                skipped += 1
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
                user_ref = f"@{user.username}" if user.username else str(user.user_id)
                print(f"[{rt.name}] ошибка для user {user_ref}: {exc}")

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
