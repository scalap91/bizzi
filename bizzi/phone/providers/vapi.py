"""Provider Vapi.ai — voice AI clé en main.
Doc : https://docs.vapi.ai/api-reference

Variables env (chargées depuis /home/ubuntu/.dashboard_vapi_creds.json par défaut) :
- VAPI_API_KEY (private key)
- VAPI_PHONE_NUMBER_ID
- VAPI_ASSISTANT_ID_FR (optionnel — assistant pré-configuré côté Vapi)
"""
import json
import os
import httpx
from typing import Optional
from ..provider import PhoneProvider, CallRequest, CallResult

VAPI_BASE = "https://api.vapi.ai"
CREDS_PATH = "/home/ubuntu/.dashboard_vapi_creds.json"


def _load_creds() -> dict:
    if os.path.exists(CREDS_PATH):
        with open(CREDS_PATH) as f:
            return json.load(f)
    return {}


class VapiProvider(PhoneProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        phone_number_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
    ):
        creds = _load_creds()
        self.api_key = api_key or os.environ.get("VAPI_API_KEY") or creds.get("private_key")
        self.phone_number_id = (
            phone_number_id
            or os.environ.get("VAPI_PHONE_NUMBER_ID")
            or creds.get("phone_number_id")
        )
        self.default_assistant_id = (
            assistant_id
            or os.environ.get("VAPI_ASSISTANT_ID_FR")
            or creds.get("assistant_id_fr_lesdemocrates")
        )
        if not self.api_key or not self.phone_number_id:
            raise RuntimeError("VAPI creds manquants (api_key + phone_number_id)")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def place_call(self, req: CallRequest) -> CallResult:
        """Démarre un appel Vapi. Mode async : retourne dès que l'appel est queued.
        Si req.assistant_id est fourni, on réutilise l'assistant Vapi existant.
        Sinon on construit un assistant inline depuis req.agent_prompt + req.voice_id.
        """
        body: dict = {
            "phoneNumberId": self.phone_number_id,
            "customer": {"number": req.to_phone},
            "metadata": req.metadata or {},
        }
        assistant_id = req.assistant_id or self.default_assistant_id
        if assistant_id:
            body["assistantId"] = assistant_id
            if req.agent_prompt or req.legal_disclaimer:
                body["assistantOverrides"] = {
                    "variableValues": {
                        "system_prompt": req.agent_prompt,
                        "first_message": req.legal_disclaimer or "",
                    }
                }
        else:
            body["assistant"] = {
                "name": "bizzi-agent",
                "model": {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "messages": [{"role": "system", "content": req.agent_prompt}],
                },
                "voice": {"provider": "11labs", "voiceId": req.voice_id},
                "firstMessage": req.legal_disclaimer or "Bonjour…",
                "endCallFunctionEnabled": True,
                "recordingEnabled": req.record,
                "maxDurationSeconds": req.max_duration_sec,
                "language": req.language,
            }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{VAPI_BASE}/call", json=body, headers=self._headers)
            if r.status_code >= 400:
                return CallResult(
                    provider_call_id="",
                    status="failed",
                    error=f"HTTP {r.status_code}: {r.text[:500]}",
                )
            data = r.json()
        return CallResult(
            provider_call_id=data.get("id", ""),
            status=data.get("status", "queued"),
        )

    async def cancel_call(self, provider_call_id: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(
                f"{VAPI_BASE}/call/{provider_call_id}", headers=self._headers
            )
            return r.status_code in (200, 204)

    def estimate_cost(self, req: CallRequest) -> float:
        # Vapi facture ~0.10€/min combinée (LLM+TTS+STT+telephony)
        return (req.max_duration_sec / 60.0) * 0.10

    def health_check(self) -> dict:
        try:
            r = httpx.get(
                f"{VAPI_BASE}/phone-number/{self.phone_number_id}",
                headers=self._headers,
                timeout=5,
            )
            return {"ok": r.status_code == 200, "status_code": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}
