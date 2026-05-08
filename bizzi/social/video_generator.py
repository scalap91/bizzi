"""bizzi.social.video_generator — Générateur vidéo paramétrable (rapatrié AirBizness).

Origine : /var/www/airbizness/tiktok_publisher.py (hardcodé deals avion).
Ici : template + context dynamiques. Le template décrit la composition
(image de fond + liste d'overlays), le context fournit les valeurs runtime.

Usage minimal :
    from bizzi.social.video_generator import generate_video, airbizness_deal_template
    out = generate_video(
        template=airbizness_deal_template(),
        context={"origin": "PAR", "destination": "JFK", "destination_name": "New York",
                 "airline": "Air France", "price": 1499, "avg_price": 4700,
                 "background_image": "/var/www/airbizness/public/images/destinations/jfk.jpg"},
    )

Format de sortie : MP4 1080x1920 (TikTok / Reels / Shorts), durée paramétrable.
Dépend de ffmpeg (binaire système).
"""
from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_DURATION_SEC = 30
DEFAULT_SIZE = "1080x1920"
DEFAULT_FPS = 30
DEFAULT_OUTPUT_DIR = "/tmp"


def _fmt(value: Any, context: dict) -> str:
    """Substitue {placeholders} si value est un str, sinon retourne tel quel."""
    if isinstance(value, str):
        try:
            return value.format(**context)
        except (KeyError, IndexError):
            return value
    return str(value)


# Caractères qui posent problème dans drawtext text='...' (interaction
# avec le parseur du filterchain ffmpeg quand plusieurs drawtext s'enchaînent).
# Au lieu d'essayer d'échapper, on bascule sur textfile= pour ces cas.
_DRAWTEXT_TRICKY = set("'\"\\:,%")


def _build_overlay(
    overlay: dict,
    context: dict,
    textfile_dir: Optional[str] = None,
) -> Optional[str]:
    """Convertit une spec overlay en filtre ffmpeg. None si overlay désactivé.

    Pour drawtext, si le texte contient un caractère problématique
    (apostrophe, virgule, pourcent, deux-points, backslash, guillemet) et
    qu'un répertoire textfile_dir est fourni, on écrit le texte dans un
    fichier et on utilise textfile= au lieu de text= (plus robuste).
    """
    cond = overlay.get("if")
    if cond and not context.get(cond):
        return None
    kind = overlay["type"]

    if kind == "drawbox":
        return (
            f"drawbox=x={overlay.get('x', 0)}:y={overlay.get('y', 0)}"
            f":w={overlay.get('w', 'iw')}:h={overlay.get('h', 'ih')}"
            f":color={overlay.get('color', 'black@0.6')}:t={overlay.get('thickness', 'fill')}"
        )

    if kind == "drawtext":
        raw = _fmt(overlay["text"], context)
        use_textfile = textfile_dir is not None and any(c in _DRAWTEXT_TRICKY for c in raw)

        if use_textfile:
            h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
            tf = Path(textfile_dir) / f"dt_{h}.txt"
            tf.write_text(raw, encoding="utf-8")
            text_part = f"textfile={tf}:reload=0"
        else:
            text_part = f"text='{raw}'"

        parts = [
            text_part,
            f"fontcolor={overlay.get('color', 'white')}",
            f"fontsize={overlay.get('size', 40)}",
            f"x={overlay.get('x', '(w-text_w)/2')}",
            f"y={overlay.get('y', 100)}",
        ]
        if overlay.get("font"):
            parts.append(f"fontfile={overlay['font']}")
        if overlay.get("shadow"):
            parts += [
                f"shadowcolor={overlay.get('shadow_color', 'black')}",
                f"shadowx={overlay.get('shadow_x', 2)}",
                f"shadowy={overlay.get('shadow_y', 2)}",
            ]
        return "drawtext=" + ":".join(parts)

    raise ValueError(f"Unknown overlay type: {kind!r}")


def _ffmpeg_filterchain(
    template: dict,
    context: dict,
    textfile_dir: Optional[str] = None,
) -> str:
    size = template.get("size", DEFAULT_SIZE)
    w, h = size.split("x")
    chain = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
    ]
    if template.get("dim_background", True):
        dim_color = template.get("dim_color", "black@0.6")
        chain.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={dim_color}:t=fill")
    for overlay in template.get("overlays", []):
        f = _build_overlay(overlay, context, textfile_dir=textfile_dir)
        if f:
            chain.append(f)
    return ",".join(chain)


def generate_video(
    template: dict,
    context: dict,
    output_path: Optional[str] = None,
) -> Optional[str]:
    """Génère un MP4 vertical depuis template + context. Retourne le chemin ou None si erreur."""
    background = context.get("background_image") or template.get("background_image")
    if not background:
        raise ValueError("background_image manquant (context ou template)")
    background = _fmt(background, context)
    if not Path(background).exists():
        raise FileNotFoundError(f"background_image introuvable : {background}")

    duration = int(template.get("duration_sec", DEFAULT_DURATION_SEC))
    fps = int(template.get("fps", DEFAULT_FPS))

    if output_path is None:
        slug = context.get("slug") or context.get("destination") or "post"
        ts = int(datetime.now().timestamp())
        output_path = f"{DEFAULT_OUTPUT_DIR}/social_{slug}_{ts}.mp4"

    # textfile= mode requires a tmpdir for the text payloads. Auto-cleanup.
    textfile_dir = tempfile.mkdtemp(prefix="bizzi_drawtext_")
    try:
        vf = _ffmpeg_filterchain(template, context, textfile_dir=textfile_dir)
        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", background,
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(fps),
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")[-800:]
            raise RuntimeError(f"ffmpeg failed: {err}")
        return output_path
    finally:
        shutil.rmtree(textfile_dir, ignore_errors=True)


def ffmpeg_command_preview(template: dict, context: dict, output_path: str = "out.mp4") -> str:
    """Renvoie la commande ffmpeg construite (debug, sans l'exécuter)."""
    background = _fmt(context.get("background_image") or template.get("background_image", ""), context)
    duration = int(template.get("duration_sec", DEFAULT_DURATION_SEC))
    fps = int(template.get("fps", DEFAULT_FPS))
    vf = _ffmpeg_filterchain(template, context)
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", background,
        "-vf", vf, "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        output_path,
    ]
    return " ".join(shlex.quote(c) for c in cmd)


# ── Templates de référence ────────────────────────────────────────────────
def airbizness_deal_template() -> dict:
    """Template reproduisant l'overlay AirBizness original (deal Business Class).

    Context attendu : origin, destination, destination_name, airline, price,
                      avg_price, background_image, savings_pct, savings_eur.
    """
    return {
        "size": "1080x1920",
        "duration_sec": 30,
        "fps": 30,
        "dim_background": True,
        "dim_color": "black@0.6",
        "overlays": [
            {"type": "drawbox", "x": 0, "y": 0, "w": "iw", "h": 180, "color": "black@0.85"},
            {"type": "drawbox", "x": 0, "y": 1740, "w": "iw", "h": 180, "color": "black@0.85"},
            {"type": "drawtext", "text": "AirBizness", "color": "#d4ae4a", "size": 50, "y": 55, "shadow": True, "shadow_x": 2, "shadow_y": 2},
            {"type": "drawtext", "text": "Business Class Deal", "color": "white@0.7", "size": 26, "y": 115},
            {"type": "drawtext", "text": "{origin}  ->  {destination_name}", "color": "white", "size": 80, "y": 320, "shadow": True, "shadow_x": 3, "shadow_y": 3},
            {"type": "drawtext", "text": "{airline}", "color": "#d4ae4a", "size": 32, "y": 420},
            {"type": "drawtext", "text": "Business Class", "color": "white", "size": 38, "y": 560},
            {"type": "drawtext", "text": "au prix d un vol Economy", "color": "#d4ae4a", "size": 36, "y": 610},
            {"type": "drawtext", "text": "{price} EUR", "color": "white", "size": 150, "y": 680, "shadow": True, "shadow_x": 5, "shadow_y": 5},
            {"type": "drawtext", "text": "au lieu de {avg_price} EUR", "color": "white@0.6", "size": 32, "y": 850},
            {"type": "drawbox", "x": 140, "y": 920, "w": 800, "h": 110, "color": "#c0392b@0.95"},
            {"type": "drawtext", "text": "ECONOMIE DE {savings_pct} POURCENT", "color": "white", "size": 44, "y": 943, "shadow": True},
            {"type": "drawtext", "text": "Vous economisez {savings_eur} EUR", "color": "white", "size": 36, "y": 1060},
            {"type": "drawbox", "x": 140, "y": 1760, "w": 800, "h": 80, "color": "#d4ae4a@0.95"},
            {"type": "drawtext", "text": "Reserver sur AirBizness.com", "color": "black", "size": 30, "y": 1782},
        ],
    }


def lesdemocrates_article_template() -> dict:
    """Template clip 30s pour annoncer un nouvel article Les Démocrates."""
    return {
        "size": "1080x1920",
        "duration_sec": 30,
        "fps": 30,
        "dim_background": True,
        "dim_color": "black@0.7",
        "overlays": [
            {"type": "drawbox", "x": 0, "y": 0, "w": "iw", "h": 180, "color": "black@0.85"},
            {"type": "drawbox", "x": 0, "y": 1740, "w": "iw", "h": 180, "color": "black@0.85"},
            {"type": "drawtext", "text": "Les Democrates", "color": "white", "size": 56, "y": 60, "shadow": True},
            {"type": "drawtext", "text": "{category}", "color": "#3498db", "size": 30, "y": 130},
            {"type": "drawtext", "text": "{title}", "color": "white", "size": 64, "y": 600, "shadow": True, "shadow_x": 3, "shadow_y": 3},
            {"type": "drawtext", "text": "{subtitle}", "color": "white@0.85", "size": 38, "y": 900},
            {"type": "drawbox", "x": 140, "y": 1760, "w": 800, "h": 80, "color": "#3498db@0.95"},
            {"type": "drawtext", "text": "Lire sur lesdemocrates.fr", "color": "white", "size": 30, "y": 1782},
        ],
    }


def onyx_scoop_template() -> dict:
    """Template clip 60s scoop Onyx Infos avec sources."""
    return {
        "size": "1080x1920",
        "duration_sec": 60,
        "fps": 30,
        "dim_background": True,
        "dim_color": "black@0.7",
        "overlays": [
            {"type": "drawbox", "x": 0, "y": 0, "w": "iw", "h": 180, "color": "#000000@0.9"},
            {"type": "drawbox", "x": 0, "y": 1740, "w": "iw", "h": 180, "color": "#000000@0.9"},
            {"type": "drawtext", "text": "ONYX INFOS", "color": "#e74c3c", "size": 60, "y": 55, "shadow": True},
            {"type": "drawtext", "text": "SCOOP FACT-CHECKE", "color": "white", "size": 26, "y": 130},
            {"type": "drawtext", "text": "{headline}", "color": "white", "size": 60, "y": 500, "shadow": True, "shadow_x": 3, "shadow_y": 3},
            {"type": "drawtext", "text": "Sources :", "color": "#e74c3c", "size": 36, "y": 1100},
            {"type": "drawtext", "text": "{sources}", "color": "white@0.85", "size": 28, "y": 1160},
            {"type": "drawbox", "x": 140, "y": 1760, "w": 800, "h": 80, "color": "#e74c3c@0.95"},
            {"type": "drawtext", "text": "onyx-infos.fr", "color": "white", "size": 30, "y": 1782},
        ],
    }


# Registry exporté pour templates.py (résolution par id)
BUILTIN_TEMPLATES: dict[str, dict] = {
    "airbizness_deal": airbizness_deal_template(),
    "lesdemocrates_article": lesdemocrates_article_template(),
    "onyx_scoop": onyx_scoop_template(),
}
