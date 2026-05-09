#!/bin/bash
# Wrapper qui charge DATABASE_URL depuis la conf supervisor bizzi-api
# (evite de dupliquer le secret dans plusieurs fichiers)
set -euo pipefail

DATABASE_URL="$(grep -oP 'DATABASE_URL="\K[^"]+' /etc/supervisor/conf.d/bizzi.conf | head -1)"
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL introuvable dans /etc/supervisor/conf.d/bizzi.conf" >&2
    exit 2
fi
export DATABASE_URL

exec /opt/bizzi/bizzi/venv/bin/python /opt/bizzi/bizzi/scripts/static_publisher.py "$@"
