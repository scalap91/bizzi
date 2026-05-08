"""Provider SMS OVH — Phase 0 stub.

À implémenter Phase 1 : API OVH /sms/{serviceName}/jobs (signature consumer key).
"""
from __future__ import annotations

from ..base import SmsProvider, SmsRequest, SmsResult


class OvhSmsProvider(SmsProvider):
    name = "ovh"

    async def send(self, req: SmsRequest) -> SmsResult:
        raise NotImplementedError("OvhSmsProvider.send — Phase 1")

    def estimate_cost(self, req: SmsRequest) -> float:
        segments = max(1, (len(req.body) + 159) // 160)
        return 0.055 * segments  # tarif indicatif OVH FR

    def health_check(self) -> dict:
        return {"ok": False, "provider": self.name, "status": "stub"}
