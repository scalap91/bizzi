"""Provider Instagram (Graph API) — squelette Phase 0.

Doc : https://developers.facebook.com/docs/instagram-api/guides/content-publishing
Flow : POST /{ig-user-id}/media (container) → POST /{ig-user-id}/media_publish.
Requiert un Instagram Business Account lié à une Page Facebook.
"""
from __future__ import annotations

import os
from typing import Optional

from ..publisher import PostProvider, PostRequest, PostResult


class InstagramProvider(PostProvider):
    name = "instagram"

    def __init__(self, access_token: Optional[str] = None, ig_user_id: Optional[str] = None):
        self.access_token = access_token or os.environ.get("INSTAGRAM_ACCESS_TOKEN")
        self.ig_user_id = ig_user_id or os.environ.get("INSTAGRAM_USER_ID")

    async def publish(self, req: PostRequest) -> PostResult:
        if not self.access_token or not self.ig_user_id:
            return PostResult(network=self.name, status="queued",
                              error="Instagram credentials missing — shadow only")
        return PostResult(network=self.name, status="queued",
                          error="Instagram provider not implemented yet (Phase 1)")

    async def fetch_metrics(self, provider_post_id: str) -> dict:
        return {"error": "not implemented"}

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "configured": bool(self.access_token and self.ig_user_id),
        }
