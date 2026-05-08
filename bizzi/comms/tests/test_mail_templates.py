"""Tests templates mail (subject + html + text)."""
from __future__ import annotations

import pytest

from comms.mail import templates as t
from comms import _template


def test_render_inline_subject_only_html():
    out = t.render_inline(
        subject="Hi {{ name }}", html="<p>Hi {{ name }}</p>", context={"name": "Alice"},
    )
    assert out.subject == "Hi Alice"
    assert out.html == "<p>Hi Alice</p>"
    assert out.text is None


def test_render_inline_text_only():
    out = t.render_inline(
        subject="x", text="Bonjour {{ name }}", context={"name": "Bob"},
    )
    assert out.text == "Bonjour Bob"
    assert out.html is None


def test_render_inline_requires_body():
    with pytest.raises(ValueError):
        t.render_inline(subject="x", context={})


def test_render_inline_requires_subject():
    with pytest.raises(ValueError):
        t.render_inline(subject="", text="x", context={})


def test_render_inline_missing_var():
    with pytest.raises(ValueError):
        t.render_inline(subject="{{ unknown }}", text="x", context={})


def test_render_with_yaml_tenant(tmp_path, monkeypatch):
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake_tenant.yaml").write_text(
        "comms:\n"
        "  mail:\n"
        "    templates:\n"
        "      welcome:\n"
        "        subject: \"Bienvenue {{ name }}\"\n"
        "        html: \"<h1>Hi {{ name }}</h1>\"\n"
        "        text: \"Hi {{ name }}\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(t, "YAML_DIR", str(yaml_dir))
    monkeypatch.setattr(_template, "YAML_DIR", str(yaml_dir))
    t.reload_tenant_yaml()

    assert t.list_templates("fake_tenant") == ["welcome"]
    out = t.render("fake_tenant", "welcome", {"name": "Bob"})
    assert out.subject == "Bienvenue Bob"
    assert out.html == "<h1>Hi Bob</h1>"
    assert out.text == "Hi Bob"

    with pytest.raises(KeyError):
        t.render("fake_tenant", "unknown", {})


def test_render_template_missing_body(tmp_path, monkeypatch):
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake.yaml").write_text(
        "comms:\n  mail:\n    templates:\n      bad:\n        subject: x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(t, "YAML_DIR", str(yaml_dir))
    monkeypatch.setattr(_template, "YAML_DIR", str(yaml_dir))
    t.reload_tenant_yaml()
    with pytest.raises(ValueError):
        t.render("fake", "bad", {})
