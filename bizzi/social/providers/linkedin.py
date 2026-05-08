"""Provider LinkedIn — squelette Phase 0.

Doc : https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
POST https://api.linkedin.com/rest/posts (avec X-Restli-Protocol-Version: 2.0.0).
Media upload : initializeUpload + PUT upload URL.
"""
from __future__ import annotations

import os
from typing import Optional

from ..publisher import PostProvider, PostRequest, PostResult


class LinkedInProvider(PostProvider):
    name = "linkedin"

    def __init__(
        self,
        access_token: Optional[str] = None,
        author_urn: Optional[str] = None,
    ):
        self.access_token = access_token or os.environ.get("LINKEDIN_ACCESS_TOKEN")
        self.author_urn = author_urn or os.environ.get("LINKEDIN_AUTHOR_URN")

    async def publish(self, req: PostRequest) -> PostResult:
        if not self.access_token or not self.author_urn:
            return PostResult(network=self.name, status="queued",
                              error="LinkedIn credentials missing — shadow only")
        return PostResult(network=self.name, status="queued",
                          error="LinkedIn provider not implemented yet (Phase 1)")

    async def fetch_metrics(self, provider_post_id: str) -> dict:
        return {"error": "not implemented"}

    def health_check(self) -> dict:
        return {
            "provider": self.name,
            "configured": bool(self.access_token and self.author_urn),
        }
