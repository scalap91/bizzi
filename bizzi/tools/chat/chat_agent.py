"""tools/chat/chat_agent.py — Agent de chat multi-tenant Claude API.

- Charge la conf tenant via tenant_db.load_tenant(slug)
- Génère dynamiquement les tools Anthropic depuis les QueryDef du tenant
- Utilise Claude Haiku 4.5 (configurable par tenant) avec prompt caching ephemeral
- Rate-limit en mémoire (par tenant + session)
- Logs structurés JSON dans /var/log/bizzi-chat.log
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from tenant_db import load_tenant, TenantDBProvider

logger = logging.getLogger("tools.chat")

CHAT_LOG_PATH = "/var/log/bizzi-chat.log"

# Tarif Haiku 4.5 (USD par token) — input $1/1M, output $5/1M
PRICE_INPUT_PER_TOKEN = 1e-6
PRICE_OUTPUT_PER_TOKEN = 5e-6

DEFAULT_SYSTEM_PROMPT = (
    "Tu es un agent conversationnel professionnel et serviable.\n"
    "{persona}\n"
    "Tu peux interroger la base via les tools fournis pour répondre précisément.\n"
    "Réponds en français, concis (3-5 phrases max), professionnel."
)


def _infer_type(param_name: str) -> dict[str, Any]:
    """Inférence simple du type JSON Schema depuis le nom du param."""
    name = param_name.lower()
    if "email" in name:
        return {"type": "string", "format": "email"}
    if any(k in name for k in ("price", "max", "count", "num", "amount")):
        return {"type": "number"}
    return {"type": "string"}


def _log_json(entry: dict[str, Any]) -> None:
    """Append une entrée JSON au log chat."""
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(CHAT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.warning(f"chat log write failed: {e}")


class ChatAgent:
    """Agent de chat multi-tenant utilisant Claude API + tools dynamiques."""

    def __init__(self, tenant_id: str, session_id: str):
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.tenant: TenantDBProvider = load_tenant(tenant_id)
        self.history: list[dict] = []
        self._rl = {
            "count": 0,
            "tokens": 0,
            "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

    # ─── Construction prompt + tools ──────────────────────────────────────

    def _build_system(self) -> str:
        sp = self.tenant.config.system_prompt or DEFAULT_SYSTEM_PROMPT
        persona = self.tenant.config.agent_persona or ""
        return sp.replace("{persona}", persona)

    def _build_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        queries = list(self.tenant.config.queries.values())
        for i, q in enumerate(queries):
            t: dict[str, Any] = {
                "name": q.name,
                "description": q.description or f"Query {q.name}",
                "input_schema": {
                    "type": "object",
                    "properties": {p: _infer_type(p) for p in q.params},
                    "required": list(q.params),
                },
            }
            # cache_control ephemeral sur le dernier tool pour activer le cache
            if i == len(queries) - 1:
                t["cache_control"] = {"type": "ephemeral"}
            tools.append(t)
        return tools

    # ─── Rate-limit ────────────────────────────────────────────────────────

    def _check_rate_limit(self) -> dict | None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._rl["day"] != today:
            self._rl = {"count": 0, "tokens": 0, "day": today}
        rl = self.tenant.config.rate_limit
        if self._rl["count"] >= rl.max_per_day:
            return {
                "error": "rate_limit_exceeded",
                "details": f"max_per_day={rl.max_per_day} atteint pour {self.tenant_id}/{self.session_id}",
            }
        if self._rl["tokens"] >= rl.max_tokens_per_day:
            return {
                "error": "rate_limit_exceeded",
                "details": f"max_tokens_per_day={rl.max_tokens_per_day} atteint",
            }
        return None

    # ─── Boucle agent principale ───────────────────────────────────────────

    async def reply(self, message: str) -> dict:
        rl_err = self._check_rate_limit()
        if rl_err:
            return rl_err

        self.history.append({"role": "user", "content": message})

        client = anthropic.Anthropic()
        cfg = self.tenant.config
        llm = cfg.llm
        system_blocks = [
            {
                "type": "text",
                "text": self._build_system(),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        tools = self._build_tools()

        tools_called: list[str] = []
        in_tokens = 0
        out_tokens = 0
        cache_creation = 0
        cache_read = 0
        text_response = ""
        t0 = time.perf_counter()
        last_resp = None

        for _iter in range(5):
            try:
                resp = await asyncio.to_thread(
                    client.messages.create,
                    model=llm.model,
                    max_tokens=llm.max_tokens,
                    temperature=llm.temperature,
                    system=system_blocks,
                    tools=tools,
                    messages=self.history,
                )
            except Exception as e:
                logger.exception(f"[{self.tenant_id}] anthropic call failed")
                return {
                    "error": "anthropic_error",
                    "details": f"{type(e).__name__}: {e}",
                }

            last_resp = resp
            try:
                u = resp.usage
                in_tokens += getattr(u, "input_tokens", 0) or 0
                out_tokens += getattr(u, "output_tokens", 0) or 0
                cache_creation += getattr(u, "cache_creation_input_tokens", 0) or 0
                cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
            except Exception:
                pass

            self.history.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "tool_use":
                tool_results = []
                for block in resp.content:
                    if getattr(block, "type", None) == "tool_use":
                        tname = block.name
                        tinput = block.input or {}
                        tools_called.append(tname)
                        try:
                            result = self.tenant.execute(tname, tinput)
                        except Exception as e:
                            result = {"error": f"tool_exec_failed: {type(e).__name__}: {e}"}
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        })
                self.history.append({"role": "user", "content": tool_results})
                continue
            else:
                parts = []
                for block in resp.content:
                    if getattr(block, "type", None) == "text":
                        parts.append(block.text)
                text_response = "\n".join(parts).strip()
                break
        else:
            text_response = text_response or "(Désolé, je n'ai pas pu finaliser la réponse en 5 itérations.)"

        duration_ms = int((time.perf_counter() - t0) * 1000)
        cost = in_tokens * PRICE_INPUT_PER_TOKEN + out_tokens * PRICE_OUTPUT_PER_TOKEN

        self._rl["count"] += 1
        self._rl["tokens"] += in_tokens + out_tokens

        log_entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tenant": self.tenant_id,
            "session_id": self.session_id,
            "model": llm.model,
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "cost_estimated": round(cost, 6),
            "tools_called": tools_called,
            "duration_ms": duration_ms,
        }
        _log_json(log_entry)

        return {
            "session_id": self.session_id,
            "tenant": self.tenant_id,
            "response": text_response,
            "tools_called": tools_called,
            "tokens": {"in": in_tokens, "out": out_tokens},
            "cost_estimated": round(cost, 6),
            "model": llm.model,
        }


# ─── Sessions globales ────────────────────────────────────────────────────

SESSIONS: dict[tuple[str, str], ChatAgent] = {}


def get_session(session_id: str, tenant_id: str) -> ChatAgent:
    key = (tenant_id, session_id)
    if key not in SESSIONS:
        SESSIONS[key] = ChatAgent(tenant_id=tenant_id, session_id=session_id)
    return SESSIONS[key]
