# mm-qwen-bot — Контекст проекта

## Что это

Mattermost-бот для общения с AI-ассистентом. Поддерживает переключаемый backend:
- **Qwen Code** (`qwen -p -o stream-json --yolo`)
- **Claude** (`claude --print --verbose --output-format stream-json`)

Переключение через переменную окружения `AI_BACKEND=qwen|claude`.

## Структура репозитория

```
mm-qwen-bot/
├── bot/
│   ├── __init__.py           # Пакет
│   ├── main.py               # Точка входа: MM WebSocket слушатель
│   ├── handlers.py           # Обработка сообщений, команд, файлов
│   ├── universal_runner.py   # Универсальный runner (Claude/Qwen)
│   ├── session.py            # Управление сессиями, state.json
│   └── stt.py                # Speech-to-Text через Groq Whisper
├── tools/
│   └── rename_bot.py         # Переименование бота в MM
├── data/                     # НЕ в git — создаётся на сервере
│   ├── projects.json         # Конфиг проектов
│   └── state.json            # Сессии (session_id, mode, backend)
├── QWEN.md                   # Этот файл — контекст для AI
├── README.md                 # Документация для GitHub
├── CONTRIBUTING.md           # Гайд для контрибьюторов
├── MIGRATION.md              # Миграция с mm-claude-bot
├── .env.example              # Шаблон env
├── .gitignore
├── LICENSE                   # MIT
├── requirements.txt
├── start.sh
└── example-projects.json
```

## Как работает

### Архитектура

```
Mattermost (WebSocket) → handlers.py → universal_runner.py → Qwen/Claude CLI
                                              ↓
                                        stream-json ←─── response
```

1. **main.py** подключается к Mattermost WebSocket, слушает сообщения
2. **handlers.py** маршрутизирует: команды (`!go`, `!discuss`, `!new`) или текст
3. **universal_runner.py** запускает CLI subprocess с правильными флагами
4. Оба backend возвращают stream-json → парсим → отправляем ответ в MM

### Ключевые решения

#### universal_runner.py
- Оба backend используют `stream-json` формат (идентичный)
- Qwen: `--yolo` + `--exclude-tools` для безопасности (не `--approval-mode`)
- Claude: `--dangerously-skip-permissions` или `--disallowedTools`
- `_find_binary()` ищет в: env override → PATH → `~/.npm-global/bin` → `~/.local/bin`
- Контекст проекта: AI сам читает QWEN.md/CLAUDE.md через `read_file` в первом ходу

#### handlers.py
- Backend задаётся через `AI_BACKEND` env var (глобально, не per-project)
- При смене backend: если `session.backend != DEFAULT_BACKEND` → предупреждение, не обрабатывать сообщение
- `!new` сбрасывает session_id и backend
- Язык: префикс к каждому сообщению пользователя — «respond in Russian»
- Файлы: аудио → Groq Whisper STT, изображения → read_file, код → inline

#### session.py
- `ThreadSession` хранит: `session_id`, `mode`, `backend`
- При загрузке state.json старый backend = «» (пусто)
- Qwen session_id = UUID (36 chars, 4 дефиса). Claude — другой формат.
- Продолжение сессии только если backend совпадает

### Команды бота

| Команда | Описание |
|---------|----------|
| `!go` | Work mode — AI может редактировать файлы |
| `!discuss` | Discuss mode — только чтение |
| `!new` | Новая сессия (сброс session_id + backend) |
| `!stop` | Остановить текущий запрос |
| `!status` | Показать текущий backend, mode, проект |
| `!reload` | Перечитать projects.json |
| `!help` | Справка |

### Переключение backend

```bash
# В .env файле:
AI_BACKEND=qwen    # или claude
# Перезапуск бота
```

При первом обращении к старой сессии бот отвечает:
> ⚠️ Этот диалог был начат с **Claude**.
> Сейчас бот работает на **Qwen**.
> Чтобы продолжить — переключи бот обратно на Claude
> Или используй `!new` чтобы начать новую сессию с Qwen.

## Сервер (192.168.1.100)

### Пути
- **Исходники**: `/home/p_tikhomirov/projects/linux_workstation_server/mm-qwen-bot/`
- **Deploy**: `/home/p_tikhomirov/apps/mm-qwen-bot/` (симлинки на bot/, start.sh, requirements.txt, QWEN.md)
- **Data**: `/home/p_tikhomirov/apps/mm-qwen-bot/data/` (projects.json, state.json)
- **Tokens**: `/home/p_tikhomirov/.tokens/mm-qwen-bot.env` → symlink `.env`

### Git-репозитории

| Где | URL | Назначение |
|-----|-----|------------|
| GitHub | `origin` → `git@github.com:patihomirov/mm-qwen-bot.git` | Публичный (зеркало) |
| Forgejo | `forgejo` → `http://localhost:3001/sc/mm-qwen-bot.git` | Приватный (основной) |

**Forgejo**: `http://localhost:3001/sc/` — приватный Git-сервер (31 репозиторий).
Токен: `/home/p_tikhomirov/.tokens/forgejo.env`

### Автодплей (Forgejo → Deploy Agent → Сервис)

```
Пуш в Forgejo (master/main)
    ↓
Webhook → http://localhost:8080/deploy
    ↓
Deploy Agent:
  1. Проверяет ветку (только master/main)
  2. git pull → build/deploy
  3. notify-mattermost.sh → #admin
```

| Репозиторий | Ветка | Тип деплоя |
|-------------|-------|------------|
| `linux_workstation_server` | master | go-binary → `telegram-bot.service` |
| `mm-qwen-bot` | main | python → pip install → restart |
| `ono_slomalos` | master | script → `python3 tools/publish.py` |
| `voice2doc_bot` | master | docker compose |
| `ai-secretar` | master | docker compose |
| `forwarder-bot` | main | docker compose |
| `MR-reviewer` | master | go-binary → restart service |

Конфиг: `~/projects/linux_workstation_server/deploy-config.json`
Агент: `~/projects/linux_workstation_server/cmd/deploy-agent/deploy-agent`
Сервис: `deploy-agent.service` (systemd, порт 8080)

### Установка
```bash
# Qwen Code
npm install -g @qwen-code/qwen-code  # → ~/.npm-global/bin/qwen
qwen auth qwen-oauth                 # OAuth настроен, Free tier

# Python venv
cd ~/apps/mm-qwen-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Запуск
nohup python -m bot.main > /dev/null 2>&1 &
```

### .env (prod)
```
MM_URL=http://localhost:8065
MM_BOT_TOKEN=sd9i35gatbng78q6rns3wpsj8h
MM_OWNER_USERNAME=admin
GROQ_API_KEY=gsk_...
QWEN_PATH=/home/p_tikhomirov/.npm-global/bin/qwen
STT_LANGUAGE=ru
AI_BACKEND=qwen
```

### Mattermost
- Bot user: `@ai-assistant` (display: «AI Assistant»)
- Старый `@claude-bot` — деактивирован
- Проекты: book, server, secretar, admin

## Что делать при доработке

1. **Добавить новый backend**:
   - Константа в `universal_runner.py` → `BACKEND_NEW`
   - Логика в `_build_args()` для нового backend
   - Маппинг в `BACKEND_DISPLAY` в `handlers.py`

2. **Изменить поведение**:
   - `handlers.py` — маршрутизация, команды, обработка файлов
   - `universal_runner.py` — флаги CLI, system prompt
   - `session.py` — структура сессии

3. **Обновить контекст**: после значительных изменений обнови этот файл

## История разработки (2026-04-10)

### Проблемы и решения (итерации)

1. **PATH**: `qwen` не найден — `~/.npm-global/bin` не в PATH процесса
   → `_find_binary()` проверяет env и стандартные пути

2. **Non-interactive**: `--approval-mode auto-edit` не работает с `-p` + stdin
   → `--yolo` + `--exclude-tools` для безопасности

3. **Флаги**: `--disallowed-tools` не принят Qwen
   → Qwen использует `--exclude-tools`

4. **Язык**: системный промпт не переопределяет язык ответа
   → Префикс к сообщению пользователя: «respond in Russian»

5. **Backend scope**: сначала per-project в projects.json, потом глобальный env
   → `AI_BACKEND` env var + рестарт

6. **Сессии при смене backend**: сначала авто-сброс, потом предупреждение
   → Если mismatch → «переключи обратно или !new»

7. **Контекст QWEN.md/CLAUDE.md**: сначала автозагрузка → лишние токены
   → Инструкция в system prompt — AI сам читает через `read_file`

8. **Переименование**: `claude` → `ai-assistant`, `claude-bot` деактивирован

## TODO / Идеи

- [ ] Интеграция с Mattermost slash commands (не только bot messages)
- [ ] Поддержка MCP servers
- [ ] Тесты (mock Mattermost WebSocket)
- [ ] Dockerfile для простого деплоя
- [ ] Rate limiting / cost tracking
- [ ] Telegram → Mattermost миграция (переезд бота)
