"""Tests du mini-renderer SMS (pas de DB, pas de yaml réel)."""
from __future__ import annotations

import pytest

from comms.sms import templates as t


def test_render_inline_simple():
    out = t.render_inline("Bonjour {{ name }}", {"name": "Alice"})
    assert out == "Bonjour Alice"


def test_render_inline_dot_path():
    out = t.render_inline("RDV {{ rdv.date }} à {{ rdv.heure }}", {"rdv": {"date": "12/05", "heure": "14h"}})
    assert out == "RDV 12/05 à 14h"


def test_render_inline_missing_variable_raises():
    with pytest.raises(ValueError):
        t.render_inline("Hello {{ unknown }}", {"name": "Alice"})


def test_render_inline_no_variable():
    out = t.render_inline("texte fixe", {"foo": "bar"})
    assert out == "texte fixe"


def test_render_inline_multiple_occurrences():
    out = t.render_inline("{{ x }}-{{ x }}-{{ y }}", {"x": "A", "y": "B"})
    assert out == "A-A-B"


def test_render_inline_whitespace_around_var():
    out = t.render_inline("{{  spaced  }}", {"spaced": "ok"})
    assert out == "ok"


def test_render_inline_var_none_raises():
    with pytest.raises(ValueError):
        t.render_inline("{{ x }}", {"x": None})


def test_render_with_yaml_tenant(tmp_path, monkeypatch):
    """Charge un faux yaml tenant et vérifie render() depuis le yaml."""
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake_tenant.yaml").write_text(
        "comms:\n"
        "  sms:\n"
        "    templates:\n"
        "      hello:\n"
        "        body: \"Hi {{ name }}!\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(t, "YAML_DIR", str(yaml_dir))
    t.reload_tenant_yaml()

    assert t.list_templates("fake_tenant") == ["hello"]
    assert t.render("fake_tenant", "hello", {"name": "Bob"}) == "Hi Bob!"
    with pytest.raises(KeyError):
        t.render("fake_tenant", "unknown", {})


def test_render_template_missing_body(tmp_path, monkeypatch):
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake.yaml").write_text(
        "comms:\n  sms:\n    templates:\n      no_body:\n        note: x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(t, "YAML_DIR", str(yaml_dir))
    t.reload_tenant_yaml()
    with pytest.raises(ValueError):
        t.render("fake", "no_body", {})
