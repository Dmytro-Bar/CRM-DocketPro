#!/bin/bash
# DocketPro CRM Web — Запуск сервера
# Двічі клікніть на цей файл щоб запустити веб-версію CRM

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "🚀 Запуск DocketPro CRM Web..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Activate virtual environment
source "$DIR/venv/bin/activate"

# Open browser after 2 seconds
(sleep 2 && open http://localhost:8000) &

echo "📡 Сервер: http://localhost:8000"
echo "🛑 Зупинити: Cmd+C або закрити це вікно"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Start uvicorn
cd "$DIR/_web"
uvicorn main:app --port 8000
