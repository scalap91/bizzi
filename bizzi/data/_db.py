import os
"""Connexion DB partagée pour le module data (pattern phone/_db.py, social/_db.py).

Cette DB est la DB *moteur* Bizzi (table tenants, productions, etc.).
Pour les DB *tenant* (ex : ERP du cabinet), passer par les connecteurs
configurés dans data_sources du YAML — pas par cette helper.
"""
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
