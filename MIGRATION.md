# Миграция с mm-claude-bot на mm-qwen-bot

## Кратко

mm-qwen-bot — это backward-compatible форк mm-claude-bot с поддержкой **переключаемого backend**: Claude или Qwen Code.

## Что изменилось

| Файл | Было | Стало |
|------|------|-------|
| `bot/claude_runner.py` | Только Claude | ❌ Удалён |
| `bot/universal_runner.py` | — | ✅ Новый — поддерживает Claude и Qwen Code |
| `bot/handlers.py` | ClaudeRunner | UniversalRunner + команды `!backend` |
| `bot/session.py` | Без backend в проектах | Поле `backend` в projects.json |
| `data/projects.json` | Без backend | `"backend": "qwen"` (по умолчанию) |

## Новые команды

| Команда | Описание |
|---------|----------|
| `!backend` | Показать текущий backend |
| `!backend claude` | Переключить проект на Claude |
| `!backend qwen` | Переключить проект на Qwen Code |

## Миграция пошагово

### 1. Установка Qwen Code на сервере

```bash
mkdir -p ~/.npm-global
npm config set prefix '~/.npm-global'
echo 'export PATH=~/.npm-global/bin:$PATH' >> ~/.bashrc
source ~/.bashrc
npm install -g @qwen-code/qwen-code
qwen auth login  # настроить аутентификацию
```

### 2. Деплой бота

```bash
# Исходники
cd ~/projects/linux_workstation_server/mm-qwen-bot

# Deploy
cd ~/apps/mm-qwen-bot
ln -sf ../../projects/linux_workstation_server/mm-qwen-bot/bot bot
ln -sf ../../projects/linux_workstation_server/mm-qwen-bot/start.sh start.sh

# Venv (если ещё нет)
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Настройка projects.json

```json
{
  "project_key": {
    "name": "Имя проекта",
    "path": "/путь/к/проекту",
    "channel": "канал-mm",
    "backend": "qwen"
  }
}
```

### 4. Запуск

```bash
cd ~/apps/mm-qwen-bot
./start.sh
```

### 5. Переключение backend для проекта

В Mattermost канале проекта:
```
!backend qwen    # переключить на Qwen Code
!backend claude  # переключить на Claude
```

## Совместимость

- Все старые команды работают (`!go`, `!discuss`, `!new`, `!stop`, `!status`, `!reload`, `!help`)
- Существующие сессии Claude сохраняются
- Формат stream-json одинаковый для обоих backend
- Можно смешивать: один проект на Claude, другой на Qwen

## Архитектура universal_runner.py

```
UniversalRunner
├── backend = "claude" → claude --print --verbose --output-format stream-json
├── backend = "qwen"   → qwen -p -o stream-json
└── Оба возвращают одинаковый формат событий:
    ├── ToolUseEvent
    ├── TextDelta
    ├── FinalResult
    └── ErrorResult
```
