# mm-qwen-bot

Mattermost-бот для общения с AI-ассистентом (Claude / Qwen Code). Каждый канал MM = один проект.

## Управление проектами

Конфиг проектов: `data/projects.json`. Формат:
```json
{
  "ключ": {
    "name": "Отображаемое имя",
    "path": "/абсолютный/путь/к/проекту",
    "channel": "имя-канала-в-mattermost"
  }
}
```

## Как добавить проект

1. Отредактируй `data/projects.json` — добавь запись
2. Пользователь выполнит `!reload` в любом канале — бот подхватит изменения
3. Канал в Mattermost нужно создать отдельно (бот не создаёт каналы сам)

## Как удалить проект

1. Удали запись из `data/projects.json`
2. `!reload`

## Команды бота
- `!go` — режим работы (AI может редактировать файлы)
- `!discuss` — режим обсуждения (только чтение)
- `!new` — новая сессия
- `!stop` — остановить AI
- `!status` — текущий статус
- `!reload` — перечитать projects.json
- `!help` — справка

## Переключение backend

Backend задаётся через переменную окружения `AI_BACKEND`:
- `qwen` (по умолчанию) — Qwen Code
- `claude` — Claude (Anthropic)

Чтобы переключить:
1. Измени `AI_BACKEND` в `.env`
2. Перезапусти бота
3. При первом обращении к старой сессии бот предупредит что диалог был начат с другим AI

## Архитектура

```
mm-qwen-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py              # Точка входа, WebSocket слушатель MM
│   ├── handlers.py          # Обработка сообщений и команд
│   ├── universal_runner.py  # Универсальный runner (Claude/Qwen)
│   ├── session.py           # Управление сессиями и состоянием
│   └── stt.py               # Speech-to-Text через Groq Whisper
├── data/
│   ├── projects.json        # Конфиг проектов
│   └── state.json           # Состояние сессий (session_id, mode, backend)
├── tools/
│   └── rename_bot.py        # Скрипт переименования бота в MM
├── .env                     # Переменные окружения
├── requirements.txt
└── start.sh
```

## Ключевые решения

### universal_runner.py
Единый интерфейс для обоих backend. Оба используют stream-json формат:
- **Claude**: `claude --print --verbose --output-format stream-json`
- **Qwen Code**: `qwen -p -o stream-json --yolo`

Безопасность в discuss mode обеспечивается через `--disallowed-tools` / `--exclude-tools`, а не через интерактивное подтвер.

### Сессии
- Каждый поток в Mattermost = отдельная сессия AI
- `state.json` хранит `session_id`, `mode` и `backend` для каждого треда
- При смене backend бот видит mismatch и предупреждает пользователя
- `!new` сбрасывает session_id и backend — можно использовать с любым AI

### Поиск бинарников
`_find_binary()` ищет CLI в: env override → PATH → ~/.local/bin → ~/.npm-global/bin → /usr/local/bin

### Язык ответа
Ко всем сообщениям добавляется инструкция отвечать на русском языке.

## История разработки

### 2026-04-10 — Миграция с mm-claude-bot

**Проблема:** Бот работал только с Claude. Нужно поддерживать Qwen Code и переключение между ними.

**Решения и итерации:**

1. **Первоначальная идея:** `claude_runner.py` + `qwen_runner.py` + переключатель
   - Отклонено → сделали единый `universal_runner.py`

2. **Поиск бинарников:** Первый запуск упал с "No such file or directory: qwen"
   - Причина: `~/.npm-global/bin` не в PATH процесса бота
   - Решение: `_find_binary()` проверяет `QWEN_PATH` env и `~/.npm-global/bin/`

3. **Non-interactive режим Qwen:** Бот зависал без ответа
   - Причина: `--approval-mode auto-edit` не работает с `-p` + stdin
   - Решение: `--yolo` + `--exclude-tools` для безопасности в discuss mode

4. **Невалидные флаги:** `--disallowed-tools` не принят Qwen
   - Причина: Qwen использует `--exclude-tools`, не `--disallowed-tools`
   - Решение: разные флаги для Claude vs Qwen

5. **Язык ответов:** Qwen отвечал на английском
   - Причина: системный промпт не переопределяет язык
   - Решение: префикс к каждому сообщению пользователя с инструкцией отвечать на русском

6. **Backend: env vs per-project:** Сначала backend в projects.json, потом в env
   - Пользователь решил: один env var `AI_BACKEND` для всего бота
   - Удалены команды `!backend` / `!backend claude` / `!backend qwen`

7. **Сессии при смене backend:**
   - Сначала: авто-сброс session_id при смене backend
   - Пользователь: лучше предупредить и дать выбор
   - Решение: если session.backend != DEFAULT_BACKEND → предупредить и не обрабатывать сообщение

8. **QWEN.md/CLAUDE.md:** Сначала автозагрузка в system prompt
   - Пользователь: лишние токены, плюс дублирование
   - Решение: короткая инструкция в system prompt — AI сам прочитает файл через read_file

9. **Переименование бота:** `claude` → `ai-assistant`
   - Старая учётка `claude-bot` деактивирована

10. **Удаление старого бота:** mm-claude-bot удалён с сервера

### Установленные зависимости на сервере
- Qwen Code: `npm install -g @qwen-code/qwen-code` → `~/.npm-global/bin/qwen`
- OAuth: `qwen auth qwen-oauth` (настроен, Free tier, 1000 req/day)
- PATH: `~/.npm-global/bin` добавлен в `.bashrc`
