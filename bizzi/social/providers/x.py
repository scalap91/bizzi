"""Provider X (Twitter) — squelette Phase 0.

Doc : https://docs.x.com/x-api/posts/creation-of-a-post
POST https://api.x.com/2/tweets    (text + media_ids)
Media upload : v1.1 media/upload.json (chunked).
"""
from __future__ import annotations

import os
from typing import Optional

from ..publisher import PostProvider, PostRequest, PostResult


class XProvider(PostProvider):
    name = "x"

    def __init__(self, bearer_token: Optional[str] = None):
        self.bearer_token = bearer_token or os.environ.get("X_BEARER_TOKEN")

    async def publish(self, req: PostRequest) -> PostResult:
        if not self.bearer_token:
            return PostResult(network=self.name, status="queued",
                              error="X bearer token missing — shadow only")
        return PostResult(network=self.name, status="queued",
                          error="X provider not implemented yet (Phase 1)")

    async def fetch_metrics(self, provider_post_id: str) -> dict:
        return {"error": "not implemented"}

    def health_check(self) -> dict:
        return {"provider": self.name, "configured": bool(self.bearer_token)}
