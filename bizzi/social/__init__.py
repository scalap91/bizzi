"""bizzi.social — Module réseaux sociaux / vidéo Bizzi.

Capacités exposées au moteur :
- Génération vidéo verticale 1080x1920 (ffmpeg) paramétrable par template
- Multi-réseaux : TikTok, Instagram, X, LinkedIn (provider abstraction)
- Multi-tenant : config par tenant via domains/<tenant>.yaml section `social:`
- Shadow mode : Pascal valide chaque post avant publication
- Logs DB : table social_posts (tenant_id, agent_id, video_url, networks, métriques…)

Cas d'usage :
- lesdemocrates : nouvel article → clip TikTok 30s
- airbizness   : meilleur deal → promo TikTok
- onyx-infos   : scoop fact-checké → clip 60s avec sources

Endpoints REST : voir bizzi.social.routes (préfixe /api/social).
"""
from .video_generator import generate_video, BUILTIN_TEMPLATES
from .publisher import PostProvider, PostRequest, PostResult
from .social_log import (
    enqueue_post, get_post, get_pending, get_agent_posts,
    get_tenant_posts, get_calendar, update_status, attach_provider_post,
    update_metrics,
)
from .templates import (
    get_template, list_tenant_templates, get_tenant_networks,
    is_shadow_mode, load_tenant_social_config,
)
from .triggers import (
    match_triggers, on_article_published, fire_article_published,
)

__all__ = [
    "generate_video", "BUILTIN_TEMPLATES",
    "PostProvider", "PostRequest", "PostResult",
    "enqueue_post", "get_post", "get_pending", "get_agent_posts",
    "get_tenant_posts", "get_calendar", "update_status", "attach_provider_post",
    "update_metrics",
    "get_template", "list_tenant_templates", "get_tenant_networks",
    "is_shadow_mode", "load_tenant_social_config",
    "match_triggers", "on_article_published", "fire_article_published",
]
