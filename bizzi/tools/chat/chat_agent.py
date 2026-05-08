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
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from tenant_db import load_tenant, TenantDBProvider

# Module anonymizer + intent (Phase 11 chat_logs).
try:
    from tools.anonymizer import anonymize, hash_user_id, classify_message
    _ANONYMIZER_AVAILABLE = True
except Exception as _e:  # pragma: no cover — dégradé silencieux
    logging.getLogger("tools.chat").warning(
        f"anonymizer module unavailable, chat_logs DB insert disabled: {_e}"
    )
    anonymize = None  # type: ignore
    hash_user_id = None  # type: ignore
    classify_message = None  # type: ignore
    _ANONYMIZER_AVAILABLE = False

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

# ─── Métacognition Phase 1 — score de confiance ──────────────────────────
FACTUAL_KEYWORDS = (
    "combien", "quand", "qui", "où", "ou ", "vol", "réservation", "reservation",
    "prix", "tarif", "horaire", "date", "numéro", "numero", "billet",
)
FUZZY_PHRASES = (
    "je ne sais pas", "je ne suis pas sûr", "je ne suis pas sur",
    "peut-être", "peut etre", "il me semble", "je pense que", "probablement",
)
SELF_EVAL_MODEL = "claude-haiku-4-5"
SELF_EVAL_MAX_TOKENS = 10
SELF_EVAL_TOKEN_BUDGET_THRESHOLD = 500  # ne fait l'auto-eval que si budget restant > 500


def _compute_heuristic_confidence(
    question: str,
    response: str,
    tools_called: list[str],
    tool_failures: int,
) -> tuple[int, list[str]]:
    """Calcule un score 0-100 + reasons.

    Base = 70.
    +10 si tools used et tous succès (-20 si un échec).
    -10 si question factuelle et 0 tool used.
    -15 si réponse contient termes flous.
    -10 si réponse très courte.
    """
    score = 70
    reasons: list[str] = []

    # Tools usage
    if tools_called and tool_failures == 0:
        score += 10
        reasons.append(f"tools={len(tools_called)} OK")
    elif tools_called and tool_failures > 0:
        score -= 20
        reasons.append(f"tools={len(tools_called)} avec {tool_failures} échec(s)")
    elif not tools_called:
        ql = question.lower()
        if any(k in ql for k in FACTUAL_KEYWORDS):
            score -= 10
            reasons.append("question factuelle sans tool")

    # Mots flous
    rl = response.lower()
    if any(p in rl for p in FUZZY_PHRASES):
        score -= 15
        reasons.append("formulation floue")

    # Réponse courte
    response_clean = re.sub(r"[^\wÀ-ÿ]+", "", response)
    if len(response_clean) < 30:
        score -= 10
        reasons.append("réponse courte")

    score = max(0, min(100, score))
    return score, reasons


async def _self_eval_confidence(
    client: "anthropic.Anthropic",
    question: str,
    response: str,
) -> int | None:
    """2ème call Claude pour s'auto-évaluer. None si parsing fail."""
    if not response or not question:
        return None
    prompt = (
        f'Voici la question du visiteur : "{question[:500]}"\n'
        f'Voici la réponse que tu as donnée : "{response[:1500]}"\n\n'
        "Évalue ta confiance dans cette réponse de 0 à 100 :\n"
        "- 90-100 : information vérifiée par tools, cohérente\n"
        "- 70-89 : raisonnement solide mais pas vérifié\n"
        "- 50-69 : incertain, mention de doutes\n"
        "- 0-49 : probablement faux ou trop vague\n\n"
        "Réponds UNIQUEMENT par un nombre entier entre 0 et 100."
    )
    try:
        resp = await asyncio.to_thread(
            client.messages.create,
            model=SELF_EVAL_MODEL,
            max_tokens=SELF_EVAL_MAX_TOKENS,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        m = re.search(r"\d{1,3}", text)
        if not m:
            return None
        n = int(m.group(0))
        return max(0, min(100, n))
    except Exception as e:
        logger.warning(f"self_eval_confidence failed: {e}")
        return None


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
        tool_failures = 0
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
                            if isinstance(result, dict) and result.get("error"):
                                tool_failures += 1
                        except Exception as e:
                            result = {"error": f"tool_exec_failed: {type(e).__name__}: {e}"}
                            tool_failures += 1
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

        # ─── Métacognition Phase 1 — confidence ─────────────────────────
        heuristic_score, reasons = _compute_heuristic_confidence(
            question=message,
            response=text_response,
            tools_called=tools_called,
            tool_failures=tool_failures,
        )

        # auto-eval seulement si tokens budget restant > seuil
        rl = self.tenant.config.rate_limit
        tokens_remaining = max(0, rl.max_tokens_per_day - self._rl["tokens"] - in_tokens - out_tokens)
        self_eval_score: int | None = None
        if tokens_remaining > SELF_EVAL_TOKEN_BUDGET_THRESHOLD and text_response:
            try:
                self_eval_score = await _self_eval_confidence(client, message, text_response)
            except Exception as e:
                logger.warning(f"self_eval wrapper failed: {e}")

        if self_eval_score is not None:
            confidence = int(round(0.6 * heuristic_score + 0.4 * self_eval_score))
        else:
            confidence = heuristic_score

        confidence_reason = " / ".join(reasons) if reasons else "base 70"

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
            "tool_failures": tool_failures,
            "duration_ms": duration_ms,
            "confidence": confidence,
            "confidence_heuristic": heuristic_score,
            "confidence_self_eval": self_eval_score,
            "confidence_reason": confidence_reason,
        }
        _log_json(log_entry)

        # ─── Phase 11 — insert chat_logs (data-resale-ready, non-bloquant) ───
        try:
            self._persist_chat_log(
                message_user=message,
                message_agent=text_response,
                tools_called=tools_called,
                tokens_in=in_tokens,
                tokens_out=out_tokens,
                cost=cost,
                confidence=confidence,
                duration_ms=duration_ms,
                model_used=llm.model,
            )
        except Exception as e:
            logger.warning(f"chat_logs persist wrapper failed (non-bloquant): {e}")

        return {
            "session_id": self.session_id,
            "tenant": self.tenant_id,
            "response": text_response,
            "tools_called": tools_called,
            "tokens": {"in": in_tokens, "out": out_tokens},
            "cost_estimated": round(cost, 6),
            "model": llm.model,
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "confidence_breakdown": {
                "heuristic": heuristic_score,
                "self_eval": self_eval_score,
            },
        }


    # ─── Phase 11 — persistance chat_logs ─────────────────────────────────

    def _persist_chat_log(
        self,
        *,
        message_user: str,
        message_agent: str,
        tools_called: list[str],
        tokens_in: int,
        tokens_out: int,
        cost: float,
        confidence: int,
        duration_ms: int,
        model_used: str,
    ) -> None:
        """Insert un row dans chat_logs (DB bizzi).

        Garde le log fichier en parallèle (sécurité transition). En cas d'erreur
        DB / module manquant : log warning, ne casse PAS la réponse au visiteur.
        """
        if not _ANONYMIZER_AVAILABLE:
            return

        # Métadonnées tenant (industry / size_bucket / region).
        md = self.tenant.config.metadata or {}
        tenant_industry = (md.get("industry") or md.get("domain") or "other")
        tenant_size_bucket = md.get("size_bucket", "sme")
        tenant_region = md.get("region", "fr-fr")

        # Anonymisation user + agent.
        msg_user_anon, pii_user = anonymize(message_user)
        msg_agent_anon, pii_agent = anonymize(message_agent or "")
        pii_detected = bool(pii_user or pii_agent)

        # user_anon_id : hash sha256[:16] de l'email du message si présent.
        user_anon_id: str | None = None
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", message_user or "")
        if email_match:
            user_anon_id = hash_user_id(email_match.group())

        # Intent + topic_tags via Claude Haiku (avec cache 1h).
        intent_result = classify_message(message_user, tenant_industry)
        intent = intent_result.get("intent", "other")
        topic_tags = intent_result.get("topic_tags", []) or []

        # Lazy imports — ne plombe pas l'import du module si psycopg2 absent.
        try:
            import psycopg2
            from dotenv import load_dotenv as _ld
        except Exception as e:
            logger.warning(f"chat_logs: psycopg2/dotenv import failed: {e}")
            return

        _ld("/opt/bizzi/bizzi/.env")
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            logger.warning("chat_logs: DATABASE_URL missing, skip insert")
            return

        try:
            with psycopg2.connect(db_url) as _c:
                with _c.cursor() as _cur:
                    _cur.execute(
                        """
                        INSERT INTO chat_logs (
                          tenant, tenant_industry, tenant_size_bucket, tenant_region,
                          session_id, user_anon_id,
                          message_user, message_agent, message_user_anon, message_agent_anon,
                          intent, topic_tags, pii_detected,
                          agent_slug, model, tools_called,
                          tokens_in, tokens_out, cost_usd, confidence, duration_ms,
                          cgu_version
                        ) VALUES (
                          %s, %s, %s, %s,
                          %s, %s,
                          %s, %s, %s, %s,
                          %s, %s::jsonb, %s,
                          %s, %s, %s::jsonb,
                          %s, %s, %s, %s, %s,
                          %s
                        )
                        """,
                        (
                            self.tenant_id, tenant_industry, tenant_size_bucket, tenant_region,
                            self.session_id, user_anon_id,
                            message_user, message_agent, msg_user_anon, msg_agent_anon,
                            intent, json.dumps(topic_tags, ensure_ascii=False), pii_detected,
                            "support", model_used, json.dumps(tools_called, ensure_ascii=False),
                            tokens_in, tokens_out, round(cost, 6), confidence, duration_ms,
                            "v1.0",
                        ),
                    )
        except Exception as e:
            logger.warning(f"chat_logs insert failed (non-bloquant): {e}")


# ─── Sessions globales ────────────────────────────────────────────────────

SESSIONS: dict[tuple[str, str], ChatAgent] = {}


def get_session(session_id: str, tenant_id: str) -> ChatAgent:
    key = (tenant_id, session_id)
    if key not in SESSIONS:
        SESSIONS[key] = ChatAgent(tenant_id=tenant_id, session_id=session_id)
    return SESSIONS[key]
