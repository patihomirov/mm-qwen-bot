#!/bin/bash
export PATH=~/.npm-global/bin:$PATH
export QWEN_PATH=~/.npm-global/bin/qwen
cd ~/apps/mm-qwen-bot
source venv/bin/activate
exec python -m bot.main
