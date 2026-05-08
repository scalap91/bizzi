"""bizzi.comms — Module communications agents Bizzi.

Capacités exposées au moteur :
- sms      : envoi SMS sortant (rappels RDV, confirmations, notifications)
- mail     : envoi mail sortant (factures, contrats, comm formelle)
- inbound  : réception appels entrants (IVR + agent IA → ticket / RDV / transfert)
- calendar : gestion RDV (création, rappel, annulation, conflits)

Architecture provider-abstraction par sous-module (interface ABC + dataclasses
Request/Result). Pattern miroir de bizzi.phone et bizzi.social.

Configuration par tenant via domains/<tenant>.yaml section `comms:`.
Logs DB : sms_logs, mail_logs, inbound_call_logs, calendar_events.

Phase 0 : scaffolding uniquement. Endpoints retournent 501. Aucun routeur n'est
encore branché dans api/main.py — branchement = action prod (validation Pascal).
"""

__all__ = ["sms", "mail", "inbound", "calendar"]
