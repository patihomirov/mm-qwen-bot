# Contributing to mm-qwen-bot

## Разработка

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Добавление нового backend

Чтобы добавить поддержку нового AI backend:

1. Добавьте константу в `bot/universal_runner.py`:
   ```python
   BACKEND_NEW = "new-backend"
   VALID_BACKENDS = {BACKEND_CLAUDE, BACKEND_QWEN, BACKEND_NEW}
   ```

2. Добавьте логику `_build_args()` для нового backend

3. Добавьте маппинг в `BACKEND_DISPLAY` в `bot/handlers.py`

4. Обновите `_get_discuss_tools()` если у нового backend другие названия инструментов

## Стиль кода

- Python 3.10+ (match/case, type hints)
- Линтер: `ruff` или `flake8`
- Форматтер: `black`

## Тестирование

Запустите бота локально с тестовым Mattermost сервером или используйте мок:

```bash
# Тест universal_runner
python -c "from bot.universal_runner import UniversalRunner; print('OK')"
```
