"""Smoke tests video_generator (pas de dépendance DB ni réseau).

Lancer :
    cd /opt/bizzi && python -m bizzi.social.tests.test_video_generator
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from bizzi.social.video_generator import (
    BUILTIN_TEMPLATES,
    _build_overlay,
    _ffmpeg_filterchain,
    airbizness_deal_template,
    ffmpeg_command_preview,
    generate_video,
)


def test_build_overlay_drawbox():
    f = _build_overlay({"type": "drawbox", "x": 10, "y": 20, "w": 100, "h": 200,
                        "color": "red@0.5"}, {})
    assert f.startswith("drawbox=") and "x=10" in f and "color=red@0.5" in f
    print("  ✓ build_overlay drawbox")


def test_build_overlay_drawtext_with_format():
    f = _build_overlay({"type": "drawtext", "text": "Hello {name}", "size": 50, "y": 100}, {"name": "Pascal"})
    assert "text='Hello Pascal'" in f and "fontsize=50" in f
    print("  ✓ build_overlay drawtext + format")


def test_build_overlay_conditional_skip():
    f = _build_overlay({"type": "drawtext", "text": "x", "if": "show_extra"}, {"show_extra": False})
    assert f is None
    print("  ✓ build_overlay conditional (skip)")


def test_filterchain_builds():
    tpl = airbizness_deal_template()
    ctx = {"origin": "PAR", "destination": "JFK", "destination_name": "New York",
           "airline": "Air France", "price": 1499, "avg_price": 4700,
           "savings_pct": 68, "savings_eur": 3201}
    chain = _ffmpeg_filterchain(tpl, ctx)
    assert "scale=1080:1920" in chain
    assert "PAR" in chain and "New York" in chain and "1499" in chain
    print("  ✓ filterchain builds")


def test_command_preview_shape():
    tpl = airbizness_deal_template()
    ctx = {"origin": "PAR", "destination": "JFK", "destination_name": "NY",
           "airline": "AF", "price": 1, "avg_price": 2, "savings_pct": 50,
           "savings_eur": 1, "background_image": "/tmp/x.jpg"}
    cmd = ffmpeg_command_preview(tpl, ctx, "out.mp4")
    assert cmd.startswith("ffmpeg") and "/tmp/x.jpg" in cmd and "1080:1920" in cmd
    print("  ✓ command_preview")


def test_builtin_registry():
    assert {"airbizness_deal", "lesdemocrates_article", "onyx_scoop"}.issubset(BUILTIN_TEMPLATES)
    print("  ✓ builtin templates registered")


def test_generate_video_real_ffmpeg():
    """Test bout-en-bout si ffmpeg dispo. Skip sinon."""
    if not shutil.which("ffmpeg"):
        print("  ⚠ ffmpeg manquant — test skipped")
        return
    with tempfile.TemporaryDirectory() as tmp:
        bg = Path(tmp) / "bg.jpg"
        # Génère un fond noir 1080x1920 via ffmpeg lui-même
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920", "-frames:v", "1", str(bg)],
            capture_output=True,
        )
        assert r.returncode == 0, f"ffmpeg bg failed: {r.stderr.decode()[-300:]}"

        ctx = {"origin": "PAR", "destination": "JFK", "destination_name": "New York",
               "airline": "Air France", "price": 1499, "avg_price": 4700,
               "savings_pct": 68, "savings_eur": 3201, "background_image": str(bg)}
        # On baisse à 2s pour aller vite
        tpl = airbizness_deal_template()
        tpl["duration_sec"] = 2

        out = generate_video(tpl, ctx, output_path=str(Path(tmp) / "out.mp4"))
        assert out and Path(out).exists() and Path(out).stat().st_size > 1024
        print(f"  ✓ generate_video real ffmpeg ({Path(out).stat().st_size} bytes)")


def test_textfile_mode_for_tricky_text():
    """Texte avec apostrophe → bascule en textfile= ; sinon text='...'."""
    with tempfile.TemporaryDirectory() as tmp:
        # ASCII safe → text='...'
        f1 = _build_overlay({"type": "drawtext", "text": "Bonjour Pascal", "y": 100},
                            {}, textfile_dir=tmp)
        assert "text='Bonjour Pascal'" in f1 and "textfile=" not in f1

        # Apostrophe → textfile=
        f2 = _build_overlay({"type": "drawtext", "text": "L'INSEE confirme", "y": 100},
                            {}, textfile_dir=tmp)
        assert "textfile=" in f2 and "text='" not in f2
    print("  ✓ textfile mode triggered by tricky chars")


def test_generate_video_with_apostrophes():
    """Repro du bug initial : 2 drawtext avec apostrophes → doit générer OK."""
    if not shutil.which("ffmpeg"):
        print("  ⚠ ffmpeg manquant — test skipped")
        return
    with tempfile.TemporaryDirectory() as tmp:
        bg = Path(tmp) / "bg.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920",
             "-frames:v", "1", str(bg)],
            capture_output=True, check=True,
        )
        tpl = {
            "size": "1080x1920", "duration_sec": 1, "fps": 30,
            "dim_background": False,
            "overlays": [
                {"type": "drawtext", "text": "Le pouvoir d'achat des Français recule",
                 "size": 50, "y": 600, "shadow": True},
                {"type": "drawtext", "text": "L'INSEE confirme une baisse de 0.7% au T1.",
                 "size": 30, "y": 900},
            ],
        }
        out = generate_video(tpl, {"background_image": str(bg)},
                             output_path=str(Path(tmp) / "out.mp4"))
        assert Path(out).exists() and Path(out).stat().st_size > 1024
        print(f"  ✓ generate_video with apostrophes ({Path(out).stat().st_size} bytes)")


def main() -> int:
    tests = [
        test_build_overlay_drawbox,
        test_build_overlay_drawtext_with_format,
        test_build_overlay_conditional_skip,
        test_filterchain_builds,
        test_command_preview_shape,
        test_builtin_registry,
        test_textfile_mode_for_tricky_text,
        test_generate_video_real_ffmpeg,
        test_generate_video_with_apostrophes,
    ]
    print("Running bizzi.social smoke tests…")
    failures = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failures += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
