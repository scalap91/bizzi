"""Registry des providers réseaux sociaux.

Tous les providers concrets implémentent bizzi.social.publisher.PostProvider.
"""
from .tiktok import TikTokProvider
from .instagram import InstagramProvider
from .x import XProvider
from .linkedin import LinkedInProvider

__all__ = ["TikTokProvider", "InstagramProvider", "XProvider", "LinkedInProvider"]


def get_provider(name: str, **kwargs):
    """Factory simple par nom."""
    name = name.lower()
    if name == "tiktok":
        return TikTokProvider(**kwargs)
    if name == "instagram":
        return InstagramProvider(**kwargs)
    if name in ("x", "twitter"):
        return XProvider(**kwargs)
    if name == "linkedin":
        return LinkedInProvider(**kwargs)
    raise ValueError(f"Unknown provider: {name!r}")
