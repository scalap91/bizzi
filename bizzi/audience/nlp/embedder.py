"""Embedding wrapper — réutilise bizzi.data.memory_vector._embed.

On NE ré-implémente PAS la logique OpenAI/pseudo : on fait suivre l'API
existante du module data, conformément à la consigne "ne ré-implémente
pas la mémoire pgvector déjà faite par bizzi-data".

Si le module data n'est pas disponible (ordre d'import inversé en test),
on retombe sur un pseudo-embedding hash-based local — clairement signalé
par mode='pseudo-local'.
"""
from __future__ import annotations

import hashlib
import struct
from typing import Tuple

from .._db import EMBED_DIM


def _local_pseudo_embed(text: str) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    while len(out) < EMBED_DIM:
        for i in range(0, len(h), 4):
            v = struct.unpack(">I", h[i:i + 4])[0]
            out.append((v / 2**32) - 0.5)
            if len(out) >= EMBED_DIM:
                break
        h = hashlib.sha256(h).digest()
    return out[:EMBED_DIM]


def embed(text: str) -> Tuple[list[float], str]:
    """Retourne (vector, mode) où mode = 'openai'|'pseudo'|'pseudo-local'."""
    if not text or not text.strip():
        return [0.0] * EMBED_DIM, "empty"
    try:
        from bizzi.data.memory_vector import _embed as data_embed  # type: ignore
        return data_embed(text)
    except Exception:  # noqa: BLE001
        try:
            from data.memory_vector import _embed as data_embed  # type: ignore
            return data_embed(text)
        except Exception:  # noqa: BLE001
            return _local_pseudo_embed(text), "pseudo-local"


def vec_to_bytes(vec: list[float]) -> bytes:
    return b"".join(struct.pack(">f", float(x)) for x in vec)


def bytes_to_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f">{n}f", b[: n * 4]))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return s / (na * nb)
