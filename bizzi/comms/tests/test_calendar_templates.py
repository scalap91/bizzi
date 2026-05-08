"""Tests templates calendar."""
from __future__ import annotations

import pytest

from comms import _template
from comms.calendar import templates as ct


def test_render_inline_simple():
    out = ct.render_inline(
        title="RDV {{ name }}", description="Notes : {{ note }}",
        location="Cabinet {{ city }}",
        duration_minutes=45, reminders_minutes=[60],
        context={"name": "Alice", "note": "1ère visite", "city": "Paris"},
    )
    assert out.title == "RDV Alice"
    assert out.description == "Notes : 1ère visite"
    assert out.location == "Cabinet Paris"
    assert out.duration_minutes == 45
    assert out.reminders_minutes == [60]


def test_render_inline_no_optional_fields():
    out = ct.render_inline(title="Hi {{ x }}", context={"x": "Bob"})
    assert out.title == "Hi Bob"
    assert out.description is None and out.location is None
    assert out.duration_minutes == 30


def test_render_inline_requires_title():
    with pytest.raises(ValueError):
        ct.render_inline(title="", context={})


def test_render_with_yaml_tenant(tmp_path, monkeypatch):
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake.yaml").write_text(
        "comms:\n"
        "  calendar:\n"
        "    templates:\n"
        "      consult:\n"
        "        title: \"Consultation {{ name }}\"\n"
        "        description: \"Type {{ t }}\"\n"
        "        duration_minutes: 60\n"
        "        reminders_minutes: [1440]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ct, "YAML_DIR", str(yaml_dir))
    monkeypatch.setattr(_template, "YAML_DIR", str(yaml_dir))
    ct.reload_tenant_yaml()

    assert ct.list_templates("fake") == ["consult"]
    out = ct.render("fake", "consult", {"name": "Alice", "t": "1ère"})
    assert out.title == "Consultation Alice"
    assert out.duration_minutes == 60
    assert out.reminders_minutes == [1440]
    with pytest.raises(KeyError):
        ct.render("fake", "unknown", {})


def test_render_template_missing_title(tmp_path, monkeypatch):
    yaml_dir = tmp_path / "domains"
    yaml_dir.mkdir()
    (yaml_dir / "fake.yaml").write_text(
        "comms:\n  calendar:\n    templates:\n      bad:\n        description: x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ct, "YAML_DIR", str(yaml_dir))
    monkeypatch.setattr(_template, "YAML_DIR", str(yaml_dir))
    ct.reload_tenant_yaml()
    with pytest.raises(ValueError):
        ct.render("fake", "bad", {})
