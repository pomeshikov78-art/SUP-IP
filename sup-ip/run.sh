#!/usr/bin/env bash
# СУП-ИП — запуск сервера (Linux). Требуется PostgreSQL и psycopg2-binary (см. README.md).
cd "$(dirname "$0")"
python3 -c "import psycopg2" 2>/dev/null || python3 -m pip install psycopg2-binary
exec python3 server.py --port 8000 "$@"
