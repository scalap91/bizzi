"""Provider TikTok — Content Posting API.

Doc : https://developers.tiktok.com/doc/content-posting-api-reference-direct-post

Endpoints :
  POST {BASE}/v2/post/publish/video/init/        — init upload (Direct Post)
  POST {BASE}/v2/post/publish/inbox/video/init/  — init upload (Inbox draft, user valide dans l'app)
  GET  {BASE}/v2/post/publish/status/fetch/      — status async

OAuth :
  Authorize : https://www.tiktok.com/v2/auth/authorize/?client_key=...&scope=video.publish,video.upload
  Token     : POST https://open.tiktokapis.com/v2/oauth/token/

Phase 0 : stub — pas d'OAuth réel, retourne 'queued' en shadow mode.
Les credentials seront chargés depuis tenant config (templates.provider_credential_ref)
quand Pascal nous filera les tokens TikTok réels.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from ..publisher import PostProvider, PostRequest, PostResult

TIKTOK_API_BASE = "https://open.tiktokapis.com"


class TikTokProvider(PostProvider):
    name = "tiktok"

    def __init__(self, access_token: Optional[str] = None, mode: str = "direct"):
        """mode = 'direct' (publication directe) ou 'inbox' (draft, user valide dans l'app)."""
        self.access_token = access_token or os.environ.get("TIKTOK_ACCESS_TOKEN")
        self.mode = mode
        self._headers = {
            "Authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "Content-Type": "application/json; charset=UTF-8",
        }

    async def publish(self, req: PostRequest) -> PostResult:
        if not self.access_token:
            return PostResult(network=self.name, status="queued",
                              error="TIKTOK_ACCESS_TOKEN missing — shadow only")
        if not req.video_path:
            return PostResult(network=self.name, status="failed", error="video_path required")

        endpoint = (
            f"{TIKTOK_API_BASE}/v2/post/publish/video/init/"
            if self.mode == "direct"
            else f"{TIKTOK_API_BASE}/v2/post/publish/inbox/video/init/"
        )
        try:
            file_size = os.path.getsize(req.video_path)
        except OSError as e:
            return PostResult(network=self.name, status="failed", error=str(e))

        body = {
            "post_info": {
                "title": (req.caption or "")[:2200],
                "privacy_level": "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": min(file_size, 10 * 1024 * 1024),
                "total_chunk_count": 1,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(endpoint, headers=self._headers, json=body)
            if r.status_code >= 400:
                return PostResult(network=self.name, status="failed",
                                  error=f"HTTP {r.status_code}: {r.text[:300]}")
            data = r.json().get("data") or {}
            publish_id = data.get("publish_id")
            upload_url = data.get("upload_url")

        if upload_url:
            with open(req.video_path, "rb") as f:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    up = await client.put(
                        upload_url,
                        content=f.read(),
                        headers={"Content-Type": "video/mp4",
                                 "Content-Range": f"bytes 0-{file_size-1}/{file_size}"},
                    )
                    if up.status_code >= 400:
                        return PostResult(network=self.name, status="failed",
                                          provider_post_id=publish_id,
                                          error=f"upload HTTP {up.status_code}")

        return PostResult(network=self.name, status="queued", provider_post_id=publish_id)

    async def fetch_metrics(self, provider_post_id: str) -> dict:
        if not self.access_token:
            return {"error": "no token"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/",
                headers=self._headers,
                json={"publish_id": provider_post_id},
            )
            return r.json() if r.status_code < 400 else {"error": r.text[:300]}

    def health_check(self) -> dict:
        return {"provider": self.name, "configured": bool(self.access_token), "mode": self.mode}
