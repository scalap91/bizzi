"""Interface abstraite des providers réseaux sociaux. Pattern miroir de phone/provider.py."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PostRequest:
    tenant_id: int
    networks: list[str]
    caption: str
    video_path: Optional[str] = None
    image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    hashtags: list[str] = field(default_factory=list)
    agent_id: Optional[int] = None
    scheduled_at: Optional[datetime] = None
    language: str = "fr"
    metadata: dict = field(default_factory=dict)


@dataclass
class PostResult:
    network: str
    status: str  # queued | posted | failed
    provider_post_id: Optional[str] = None
    post_url: Optional[str] = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    error: Optional[str] = None


class PostProvider(ABC):
    name: str  # 'tiktok' | 'instagram' | 'x' | 'linkedin'

    @abstractmethod
    async def publish(self, req: PostRequest) -> PostResult: ...

    @abstractmethod
    async def fetch_metrics(self, provider_post_id: str) -> dict: ...

    @abstractmethod
    def health_check(self) -> dict: ...
