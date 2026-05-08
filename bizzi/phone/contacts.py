"""bizzi.phone.contacts — CRUD contacts tenant-scoped.

Mappe au schéma DB réel :
  contacts(id, tenant_id, full_name, role, organization, phone, email, language,
           region_id, trust_level, consent_call, consent_recording, notes, tags jsonb,
           last_contacted_at, created_at, updated_at)
"""
import json
from typing import Optional
from ._db import get_conn


def get_contacts(tenant_id: int, limit: int = 100) -> list[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, full_name, role, organization, phone, email, trust_level,
                      consent_call, consent_recording, tags, last_contacted_at
               FROM contacts WHERE tenant_id = %s
               ORDER BY last_contacted_at DESC NULLS LAST, full_name
               LIMIT %s""",
            (tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def search_contacts(tenant_id: int, query: str, limit: int = 20) -> list[dict]:
    pattern = f"%{query}%"
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, full_name, role, organization, phone, trust_level
               FROM contacts
               WHERE tenant_id = %s
                 AND (full_name ILIKE %s OR organization ILIKE %s OR role ILIKE %s)
               ORDER BY trust_level DESC LIMIT %s""",
            (tenant_id, pattern, pattern, pattern, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def get_contact(tenant_id: int, contact_id: int) -> Optional[dict]:
    with get_conn(dict_rows=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM contacts WHERE tenant_id = %s AND id = %s",
            (tenant_id, contact_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_contact(
    tenant_id: int,
    full_name: str,
    phone: Optional[str] = None,
    role: Optional[str] = None,
    organization: Optional[str] = None,
    email: Optional[str] = None,
    trust_level: int = 50,
    tags: Optional[list] = None,
    consent_call: bool = False,
    consent_recording: bool = False,
    notes: Optional[str] = None,
) -> int:
    """Insert ou update sur (tenant_id, phone). Retourne contact_id."""
    tags_json = json.dumps(tags or [])
    with get_conn() as conn, conn.cursor() as cur:
        if phone:
            cur.execute(
                "SELECT id FROM contacts WHERE tenant_id = %s AND phone = %s",
                (tenant_id, phone),
            )
            row = cur.fetchone()
            if row:
                cid = row[0]
                cur.execute(
                    """UPDATE contacts SET full_name=%s, role=%s, organization=%s,
                         email=COALESCE(%s, email), trust_level=%s, tags=%s::jsonb,
                         consent_call=%s, consent_recording=%s,
                         notes=COALESCE(%s, notes), updated_at=now()
                       WHERE id=%s""",
                    (full_name, role, organization, email, trust_level, tags_json,
                     consent_call, consent_recording, notes, cid),
                )
                conn.commit()
                return cid
        cur.execute(
            """INSERT INTO contacts (tenant_id, full_name, role, organization, phone,
                 email, trust_level, tags, consent_call, consent_recording, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
               RETURNING id""",
            (tenant_id, full_name, role, organization, phone, email,
             trust_level, tags_json, consent_call, consent_recording, notes),
        )
        cid = cur.fetchone()[0]
        conn.commit()
        return cid
