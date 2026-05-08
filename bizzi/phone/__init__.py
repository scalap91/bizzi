"""bizzi.phone — Module téléphonie agents Bizzi.

Capacités exposées au moteur :
- Outbound calls : un agent appelle un contact pour un use_case (verify, prospect, devis, …)
- Inbound calls : numéro vert tenant, IA répond et crée ticket
- Memory + Contacts DB : chaque agent capitalise ses interactions
- Provider abstraction : Vapi / Twilio / Bland / FreeSWITCH

Use cases catalogués dans bizzi.phone.scripts/.
Configuration par tenant via yaml domains/<tenant>.yaml section `phone:`.
"""
from .orchestrator import make_call, get_active_calls, validate_pending
from .contacts import get_contacts, upsert_contact, search_contacts
from .call_log import log_call, get_call_logs, search_transcripts, get_month_spent_eur
from .memory import recall_for_agent, store_memory, search_memory

__all__ = [
    "make_call", "get_active_calls", "validate_pending",
    "get_contacts", "upsert_contact", "search_contacts",
    "log_call", "get_call_logs", "search_transcripts", "get_month_spent_eur",
    "recall_for_agent", "store_memory", "search_memory",
]
