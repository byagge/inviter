## Установка

1. Перейди в папку:
   - `cd sctipt`
2. Установи зависимости:
   - `pip install -r requirements.txt`
3. Создай `.env`:
   - `copy .env.example .env`
4. Заполни `API_ID` и `API_HASH` в `.env`.

## Структура

- `create_session.py` — создание `.session` файлов
- `collect_cli.py` — сбор пользователей в TXT
- `collect_from_messages_cli.py` — сбор авторов из истории сообщений (обход скрытого списка участников)
- `invite_cli.py` — инвайт из TXT
- `sessions/` — session-файлы
- `data/` — базы txt

## Использование

1. Создай сессию:
   - `python create_session.py session1`
2. Сбор:
   - `python collect_cli.py`
3. Сбор по авторам сообщений (если участники скрыты):
   - `python collect_from_messages_cli.py`
4. Инвайт:
   - `python invite_cli.py`
