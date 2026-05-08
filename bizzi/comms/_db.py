import os
"""Connexion DB partagée pour le module comms (psycopg2 direct, pattern phone/_db.py)."""
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = dict(
    host="localhost",
    database="bizzi",
    user="bizzi_admin",
    password=os.environ.get("DB_PASSWORD", ""),
)


def get_conn(dict_rows: bool = False):
    conn = psycopg2.connect(**DB_CONFIG)
    if dict_rows:
        conn.cursor_factory = RealDictCursor
    return conn
