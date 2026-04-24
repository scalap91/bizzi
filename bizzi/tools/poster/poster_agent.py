"""tools/poster/poster_agent.py — Génération d'affiches universelle"""
import logging, os, textwrap
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from config.domain_loader import DomainConfig

logger = logging.getLogger("tools.poster")

FORMATS = {
    "a4":     {"width": "210mm", "height": "297mm", "px_w": 794,  "px_h": 1123},
    "a3":     {"width": "297mm", "height": "420mm", "px_w": 1123, "px_h": 1587},
    "story":  {"width": "1080px","height": "1920px","px_w": 1080, "px_h": 1920},
    "banner": {"width": "1200px","height": "630px", "px_w": 1200, "px_h": 630 },
    "square": {"width": "1080px","height": "1080px","px_w": 1080, "px_h": 1080},
}

STYLES = {
    "modern":   {"bg": "#0f172a", "text": "#f1f5f9", "font": "Syne",    "layout": "centered"},
    "bold":     {"bg": "#dc2626", "text": "#ffffff",  "font": "Syne",    "layout": "left"},
    "minimal":  {"bg": "#ffffff", "text": "#0f172a",  "font": "Outfit",  "layout": "centered"},
    "retro":    {"bg": "#1a1a2e", "text": "#e94560",  "font": "Courier", "layout": "left"},
}


@dataclass
class PosterConfig:
    title:       str
    subtitle:    Optional[str]    = None
    body:        Optional[str]    = None
    footer:      Optional[str]    = None
    logo_url:    Optional[str]    = None
    format:      str              = "a4"
    style:       str              = "modern"
    accent_color:Optional[str]   = None
    org_name:    Optional[str]    = None
    date:        Optional[str]    = None
    hashtags:    Optional[list]   = None


class PosterAgent:
    """Génère des affiches HTML/PDF pour n'importe quel domaine."""

    def __init__(self, domain: DomainConfig):
        self.domain = domain
        self.output_dir = os.path.join("output", "posters")
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_html(self, cfg: PosterConfig) -> str:
        """Génère le HTML de l'affiche."""
        fmt    = FORMATS.get(cfg.format, FORMATS["a4"])
        sty    = STYLES.get(cfg.style, STYLES["modern"])
        accent = cfg.accent_color or self.domain.ui.primary_color
        org    = cfg.org_name or self.domain.name
        date   = cfg.date or datetime.utcnow().strftime("%d %B %Y")
        tags   = " ".join(f"#{t}" for t in (cfg.hashtags or [])) if cfg.hashtags else ""
        is_landscape = int(fmt["px_w"]) > int(fmt["px_h"])

        # Adapter la mise en page selon le format
        title_size  = "3.5rem"  if cfg.format in ["a4","a3"] else "2.5rem"
        body_size   = "1.1rem"  if cfg.format in ["a4","a3"] else "0.95rem"
        padding     = "48px"    if cfg.format in ["a4","a3"] else "32px"

        if is_landscape:
            title_size = "2.2rem"
            padding    = "40px"

        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@700;800;900&family=Outfit:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{fmt["width"]}; height:{fmt["height"]};
    background:{sty["bg"]}; color:{sty["text"]};
    font-family:'{sty["font"]}',sans-serif;
    display:flex; flex-direction:column; overflow:hidden;
  }}

  .poster {{
    flex:1; display:flex; flex-direction:{("row" if is_landscape else "column")};
    padding:{padding}; gap:32px; position:relative;
  }}

  /* Accent bar */
  .accent-bar {{
    position:absolute; top:0; left:0;
    {"width:8px; height:100%;" if sty["layout"] == "left" else "height:8px; width:100%;"}
    background:{accent};
  }}

  /* Decoration */
  .deco {{
    position:absolute; bottom:0; right:0;
    width:300px; height:300px; border-radius:50%;
    background:{accent}; opacity:0.06;
    transform:translate(30%,30%);
  }}
  .deco2 {{
    position:absolute; top:-80px; right:80px;
    width:160px; height:160px; border-radius:50%;
    background:{accent}; opacity:0.04;
  }}

  .content {{
    flex:1; display:flex; flex-direction:column;
    justify-content:{("center" if sty["layout"] == "centered" else "flex-start")};
    padding-left:{("20px" if sty["layout"] == "left" else "0")};
    position:relative; z-index:1;
  }}

  .org-badge {{
    display:inline-block; font-size:0.7rem; font-weight:700;
    letter-spacing:0.18em; text-transform:uppercase;
    color:{accent}; margin-bottom:24px;
    padding:5px 12px; border:1px solid {accent};
    border-radius:4px; width:fit-content;
  }}

  .title {{
    font-family:'Syne',sans-serif; font-size:{title_size};
    font-weight:900; line-height:1.1; letter-spacing:-0.03em;
    margin-bottom:20px; color:{sty["text"]};
  }}

  .title span {{ color:{accent}; }}

  .subtitle {{
    font-size:{body_size}; font-weight:500; opacity:0.75;
    margin-bottom:24px; line-height:1.5;
  }}

  .body-text {{
    font-size:calc({body_size} * 0.9); opacity:0.65;
    line-height:1.7; margin-bottom:28px;
  }}

  .hashtags {{
    font-size:0.8rem; font-weight:600;
    color:{accent}; margin-top:auto; letter-spacing:0.02em;
  }}

  .footer {{
    margin-top:auto; padding-top:24px;
    border-top:1px solid rgba(255,255,255,0.1);
    display:flex; align-items:center; justify-content:space-between;
  }}

  .footer-org {{ font-size:0.75rem; font-weight:700; opacity:0.6; text-transform:uppercase; letter-spacing:0.1em; }}
  .footer-date {{ font-size:0.72rem; opacity:0.5; font-family:'Outfit',sans-serif; }}

  {"" if not cfg.logo_url else f"""
  .logo {{ width:60px; height:60px; object-fit:contain; margin-bottom:20px; }}
  """}

  @media print {{
    body {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  }}
</style>
</head>
<body>
<div class="poster">
  <div class="accent-bar"></div>
  <div class="deco"></div>
  <div class="deco2"></div>

  <div class="content">
    {"" if not cfg.logo_url else f'<img class="logo" src="{cfg.logo_url}" alt="{org}">'}

    <div class="org-badge">⬡ {org}</div>

    <h1 class="title">{cfg.title}</h1>

    {"" if not cfg.subtitle else f'<p class="subtitle">{cfg.subtitle}</p>'}

    {"" if not cfg.body else f'<p class="body-text">{cfg.body}</p>'}

    {"" if not tags else f'<p class="hashtags">{tags}</p>'}

    <div class="footer">
      <span class="footer-org">{org}</span>
      <span class="footer-date">{date}</span>
    </div>
  </div>
</div>
</body>
</html>"""
        return html

    def save_html(self, cfg: PosterConfig) -> dict:
        """Sauvegarde l'affiche en HTML et retourne le chemin."""
        html      = self.generate_html(cfg)
        filename  = f"poster_{cfg.format}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
        filepath  = os.path.join(self.output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"[POSTER] Généré : {filename} ({cfg.format}/{cfg.style})")
        return {
            "filename":   filename,
            "filepath":   filepath,
            "format":     cfg.format,
            "style":      cfg.style,
            "size":       FORMATS[cfg.format],
            "generated_at": datetime.utcnow().isoformat(),
            "html":       html,   # Retourné pour affichage direct
        }

    async def generate(self, cfg: PosterConfig) -> dict:
        """Point d'entrée principal."""
        if cfg.format not in FORMATS:
            cfg.format = "a4"
        if cfg.style not in STYLES:
            cfg.style = "modern"
        if not cfg.org_name:
            cfg.org_name = self.domain.name
        return self.save_html(cfg)
