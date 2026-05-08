"""Interface abstraite des providers téléphonie. Chaque provider concret implémente."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class CallRequest:
    to_phone: str
    from_phone: str
    agent_prompt: str
    voice_id: str
    language: str = "fr"
    max_duration_sec: int = 600
    record: bool = True
    legal_disclaimer: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    assistant_id: Optional[str] = None

@dataclass
class CallResult:
    provider_call_id: str
    status: str
    duration_sec: int = 0
    cost_eur: float = 0.0
    transcript: str = ""
    summary: str = ""
    recording_url: Optional[str] = None
    outcome: str = ""
    error: Optional[str] = None

class PhoneProvider(ABC):
    @abstractmethod
    async def place_call(self, req: CallRequest) -> CallResult: ...

    @abstractmethod
    async def cancel_call(self, provider_call_id: str) -> bool: ...

    @abstractmethod
    def estimate_cost(self, req: CallRequest) -> float: ...

    @abstractmethod
    def health_check(self) -> dict: ...
