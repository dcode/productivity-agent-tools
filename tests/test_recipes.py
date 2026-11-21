"""The Goose recipes in contrib/goose: valid recipes that name only tools that exist."""

import re
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
RECIPES = sorted((ROOT / "contrib" / "goose").glob("*.yaml"))
TOOL = re.compile(
    r"\b((?:obsidian|jira|bitbucket|outlook_calendar|outlook_mail|servicenow)_[a-z][a-z_]*[a-z])\b"
)
DEFINED = re.compile(r"async def ([a-z]+_[a-z_]+)\(")
VARIABLE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PREFIXES = {"outlook_calendar", "outlook_mail"}


def _tools() -> set[str]:
    source = ROOT / "src" / "productivity_agent_tools"
    return {
        name
        for file in source.rglob("*.py")
        for name in DEFINED.findall(file.read_text(encoding="utf-8"))
    }


def _load(path: Path) -> dict[str, Any]:
    loaded = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_there_are_recipes() -> None:
    assert [path.name for path in RECIPES] == [
        "meeting-prep.yaml",
        "plan-day.yaml",
        "wrap-day.yaml",
    ]


@pytest.mark.parametrize("path", RECIPES, ids=lambda path: path.name)
def test_a_recipe_follows_the_goose_format(path: Path) -> None:
    recipe = _load(path)
    assert isinstance(recipe["title"], str) and recipe["title"]
    assert isinstance(recipe["description"], str) and recipe["description"]
    assert recipe.get("instructions") or recipe.get("prompt")
    assert recipe.get("prompt"), "a prompt lets the recipe run headless"
    keys = set()
    for parameter in recipe.get("parameters", []):
        assert set(parameter) <= {"key", "input_type", "requirement", "default", "description"}
        assert parameter["input_type"] in {"string", "number", "boolean", "date"}
        assert parameter["requirement"] in {"required", "optional", "user_prompt"}
        assert parameter["description"]
        if parameter["requirement"] == "optional":
            assert "default" in parameter, "Goose needs a default for an optional parameter"
        if parameter["requirement"] == "required":
            assert "default" not in parameter
        keys.add(parameter["key"])
    text = f"{recipe.get('instructions', '')}\n{recipe['prompt']}"
    assert set(VARIABLE.findall(text)) == keys


@pytest.mark.parametrize("path", RECIPES, ids=lambda path: path.name)
def test_a_recipe_names_only_real_tools(path: Path) -> None:
    recipe = _load(path)
    text = f"{recipe.get('instructions', '')}\n{recipe['prompt']}"
    named = set(TOOL.findall(text)) - PREFIXES
    assert named, "a recipe should say which tools to use"
    assert named <= _tools(), f"unknown tools: {sorted(named - _tools())}"


TEMPLATES = ROOT / "contrib" / "obsidian"


@pytest.mark.parametrize(
    ("name", "sections"),
    [
        ("Daily note.md", ["plan", "wrap-up"]),
        ("Meeting (sync).md", ["prep"]),
        ("Meeting (ad hoc).md", ["prep"]),
    ],
)
def test_the_templates_hold_the_sections_the_recipes_write(name: str, sections: list[str]) -> None:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    for section in sections:
        assert text.count(f"<!-- productivity-agent-tools:{section}:start -->") == 1
        assert text.count(f"<!-- productivity-agent-tools:{section}:end -->") == 1
        recipes = " ".join(path.read_text(encoding="utf-8") for path in RECIPES)
        assert f"section {section}" in recipes, f"no recipe writes the {section} section"


@pytest.mark.parametrize("name", ["Daily note.md", "Meeting (sync).md"])
def test_the_core_templates_fill_without_templater(name: str, tmp_path: Path) -> None:
    import datetime

    from productivity_agent_tools.obsidian.frontmatter import parse
    from productivity_agent_tools.obsidian.templates import fill

    text = (TEMPLATES / name).read_text(encoding="utf-8")
    filled = fill(text, tmp_path, "Design review", datetime.datetime(2026, 11, 19, 9, 30))
    assert "{{" not in filled and "<%" not in filled
    assert parse(filled).error is None
    assert "Thursday, November 19, 2026" in filled or "# Design review" in filled
