"""Use-case end-to-end : génère un clip TikTok depuis un deal AirBizness fictif
puis l'enregistre en shadow queue (status=pending).

Usage :
    python -m bizzi.social.scripts.airbizness_deal_clip
    python -m bizzi.social.scripts.airbizness_deal_clip --no-db   # vidéo seule, pas de DB

Prérequis :
- ffmpeg installé
- Image de fond /var/www/airbizness/public/images/destinations/<dest>.jpg
  (peut être passée via --bg)
- Table social_posts créée (migrations/001_social_posts.sql) — sauf --no-db
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from bizzi.social.video_generator import generate_video, airbizness_deal_template


def fake_deal() -> dict:
    return {
        "origin": "PAR",
        "destination": "JFK",
        "destination_name": "New York",
        "airline": "Air France",
        "price": 1499,
        "avg_price": 4700,
        "savings_pct": 68,
        "savings_eur": 3201,
        "slug": "airbizness_par_jfk",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bg", default="/var/www/airbizness/public/images/destinations/jfk.jpg")
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-db", action="store_true", help="N'enregistre pas dans social_posts")
    parser.add_argument("--tenant-id", type=int, default=999, help="tenant_id pour la shadow queue")
    args = parser.parse_args()

    if not Path(args.bg).exists():
        print(f"[!] Image de fond introuvable : {args.bg}", file=sys.stderr)
        print("    Passe --bg <chemin> ou crée une image factice (1080x1920 jpg).", file=sys.stderr)
        return 2

    ctx = fake_deal()
    ctx["background_image"] = args.bg

    print(f"[1/2] generate_video → ffmpeg ({ctx['origin']} → {ctx['destination_name']})")
    out = generate_video(airbizness_deal_template(), ctx, output_path=args.out)
    print(f"      OK : {out}  ({os.path.getsize(out)} bytes)")

    if args.no_db:
        return 0

    print("[2/2] enqueue_post → social_posts (shadow=true, status=pending)")
    from bizzi.social.social_log import enqueue_post  # import tardif pour --no-db sans DB
    post_id = enqueue_post(
        tenant_id=args.tenant_id,
        networks=["tiktok"],
        caption=f"Business Class {ctx['origin']} → {ctx['destination_name']} à {ctx['price']}€ "
                f"(au lieu de {ctx['avg_price']}€). -{ctx['savings_pct']}%.",
        video_url=out,
        hashtags=["BusinessClass", "Deal", "Voyage", "AirBizness"],
        template_id="airbizness_deal",
        context=ctx,
        shadow=True,
        created_by="airbizness_deal_clip.py",
    )
    print(f"      OK : post_id={post_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
