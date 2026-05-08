"""Tests du qualifier inbound (mock httpx.AsyncClient)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from comms.inbound import qualifier as q


class _FakeResp:
    def __init__(self, status_code: int = 200, json_data: Any = None, text: str = ""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text or ""

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, **kw):
        return self._resp


def _patch_client(monkeypatch, resp: _FakeResp):
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeClient(resp))


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_qualify_disabled_returns_safe_default():
    out = _run(q.qualify([{"role": "user", "text": "Hi"}], enabled=False))
    assert out.intent == "autre"
    assert out.suggested_action == "ticket"
    assert out.requires_human is True


def test_qualify_empty_transcript():
    out = _run(q.qualify([], enabled=True))
    assert out.intent == "autre"
    assert "vide" in out.summary


def test_qualify_ok_full_json(monkeypatch):
    body = {
        "intent": "rdv",
        "urgency": 1,
        "suggested_action": "rdv",
        "extracted": {"nom": "Alice", "demande": "RDV plombier"},
        "confidence": 0.9,
        "requires_human": False,
        "summary": "Alice veut un RDV plombier mardi.",
    }
    import json as J
    _patch_client(monkeypatch, _FakeResp(200, {"response": J.dumps(body)}))
    out = _run(q.qualify([{"role": "user", "text": "Bonjour, je veux un RDV plombier mardi"}]))
    assert out.intent == "rdv"
    assert out.urgency == 1
    assert out.suggested_action == "rdv"
    assert out.extracted["nom"] == "Alice"
    assert out.confidence == 0.9
    assert out.requires_human is False
    assert "Alice" in out.summary


def test_qualify_urgence_forces_human(monkeypatch):
    body = {"intent": "urgence", "urgency": 3, "suggested_action": "ticket",
            "requires_human": False, "confidence": 0.8, "summary": "fuite gaz"}
    import json as J
    _patch_client(monkeypatch, _FakeResp(200, {"response": J.dumps(body)}))
    out = _run(q.qualify([{"role": "user", "text": "URGENCE fuite de gaz"}]))
    # Le normalizer doit forcer requires_human=True quand urgency>=2 ou intent='urgence'
    assert out.requires_human is True
    assert out.urgency == 3


def test_qualify_invalid_intent_falls_back(monkeypatch):
    body = {"intent": "wat", "suggested_action": "wut", "urgency": "xx", "confidence": "lol"}
    import json as J
    _patch_client(monkeypatch, _FakeResp(200, {"response": J.dumps(body)}))
    out = _run(q.qualify([{"role": "user", "text": "x"}]))
    assert out.intent == "autre"
    assert out.suggested_action == "ticket"
    assert out.urgency == 0
    assert out.confidence == 0.0


def test_qualify_extracts_first_json_in_messy_response(monkeypatch):
    raw = """Voici ma réponse :
{"intent":"renseignement","urgency":0,"suggested_action":"sms_confirm","confidence":0.7,"requires_human":false,"summary":"horaires"}
Merci."""
    _patch_client(monkeypatch, _FakeResp(200, {"response": raw}))
    out = _run(q.qualify([{"role": "user", "text": "horaires?"}]))
    assert out.intent == "renseignement"
    assert out.suggested_action == "sms_confirm"


def test_qualify_no_json_in_response(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(200, {"response": "réponse sans JSON"}))
    out = _run(q.qualify([{"role": "user", "text": "x"}]))
    assert out.intent == "autre"
    assert out.suggested_action == "ticket"
    assert out.requires_human is True


def test_qualify_ollama_http_error(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(500, {}, text="server error"))
    out = _run(q.qualify([{"role": "user", "text": "x"}]))
    assert out.intent == "autre"
    assert out.requires_human is True


def test_qualify_ollama_network_error(monkeypatch):
    import httpx

    class BoomClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw):
            raise httpx.ConnectError("can't connect")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: BoomClient())
    out = _run(q.qualify([{"role": "user", "text": "x"}]))
    assert out.intent == "autre"
    assert out.requires_human is True
