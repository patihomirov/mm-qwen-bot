# mm-qwen-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Mattermost-бот для общения с AI-ассистентом с поддержкой переключаемого backend: **Claude** (Anthropic) или **Qwen Code** (Alibaba).

## Возможности

- 🔀 **Переключаемый backend** — Claude или Qwen Code через одну переменную окружения `AI_BACKEND`
- 📂 **Каждый канал Mattermost = отдельный проект** на сервере
- 💬 **Два режима работы:**
  - `!go` — work mode (AI может редактировать файлы)
  - `!discuss` — discuss mode (только чтение)
- 🔄 **Изолированные сессии** — каждый поток в Mattermost = отдельная сессия AI
- 📎 **Поддержка файлов** — аудио (STT через Groq Whisper), изображения, код
- 🎤 **Голосовые сообщения** — транскрибация через Groq Whisper API
- ⚠️ **Уведомление о смене backend** — если диалог был начат с другим AI, бот предупредит

## Быстрый старт

### Установка

```bash
git clone https://github.com/your-username/mm-qwen-bot.git
cd mm-qwen-bot

# Создаём виртуальное окружение
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Настройка

Создайте `.env` файл:

```env
MM_URL=http://your-mattermost:8065
MM_BOT_TOKEN=your-bot-token
MM_OWNER_USERNAME=your-username
GROQ_API_KEY=your-groq-key          # для транскрибации голоса (опционально)
AI_BACKEND=qwen                      # qwen или claude
QWEN_PATH=~/.npm-global/bin/qwen     # путь к qwen (если не в PATH)
STT_LANGUAGE=ru                      # язык для STT
```

### Установка Qwen Code

```bash
npm install -g @qwen-code/qwen-code
qwen auth qwen-oauth  # настройка аутентификации
```

### Установка Claude (опционально)

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

### Запуск

```bash
./start.sh
# или
python -m bot.main
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `!go` | Режим работы (AI может редактировать файлы) |
| `!discuss` | Режим обсуждения (только чтение) |
| `!new` | Новая сессия в этом потоке |
| `!stop` | Остановить AI |
| `!status` | Текущий статус |
| `!reload` | Перечитать projects.json |
| `!help` | Справка |

## Конфигурация проектов

Файл `data/projects.json`:

```json
{
  "project_key": {
    "name": "Отображаемое имя",
    "path": "/абсолютный/путь/к/проекту",
    "channel": "имя-канала-в-mattermost"
  }
}
```

## Переключение backend

Измените `AI_BACKEND` в `.env` и перезапустите бота:

```env
AI_BACKEND=qwen    # Qwen Code
AI_BACKEND=claude  # Claude
```

При переключении бот автоматически определит, если сессия была создана другим AI, и предложит пользователю выбрать: продолжить с тем же AI (переключив обратно) или начать новую сессию.

## Архитектура

```
mm-qwen-bot/
├── bot/
│   ├── main.py              # Точка входа, WebSocket слушатель Mattermost
│   ├── handlers.py          # Обработка сообщений и команд
│   ├── universal_runner.py  # Универсальный runner (Claude ↔ Qwen)
│   ├── session.py           # Управление сессиями и состоянием
│   └── stt.py               # Speech-to-Text через Groq Whisper
├── data/
│   ├── projects.json        # Конфиг проектов
│   └── state.json           # Состояние сессий
├── tools/
│   └── rename_bot.py        # Утилита переименования бота в MM
├── .env.example             # Шаблон переменных окружения
├── requirements.txt
└── start.sh
```

## Как это работает

`UniversalRunner` — единый интерфейс для обоих backend:

| Backend | Команда | Безопасность |
|---------|---------|-------------|
| Claude | `claude --print --verbose --output-format stream-json` | `--disallowedTools` |
| Qwen Code | `qwen -p -o stream-json --yolo` | `--exclude-tools` |

Оба возвращают одинаковый stream-json формат, что позволяет прозрачно переключаться.

## Требования

- Python 3.10+
- Mattermost сервер (локальный или удалённый)
- Qwen Code и/или Claude CLI с настроенной аутентификацией
- Groq API key (опционально, для транскрибации голоса)

## Лицензия

MIT
