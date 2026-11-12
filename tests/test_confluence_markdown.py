"""Markdown to Confluence storage format."""

import xml.etree.ElementTree as ET

import pytest

from productivity_agent_tools.confluence.markdown import markdown_to_storage

_NAMESPACES = 'xmlns:ac="urn:ac" xmlns:ri="urn:ri"'


def _well_formed(storage: str) -> None:
    ET.fromstring(f"<root {_NAMESPACES}>{storage}</root>")


def test_common_markdown_becomes_xhtml() -> None:
    storage = markdown_to_storage(
        "# About me\n\nI work on **uploads** & _search_.  \nNew line.\n\n"
        "- one\n- ~~two~~\n\n1. first\n\n> quoted\n\n---\n\n"
        "| a | b |\n|---|---|\n| 1 | `x<y` |\n\n[site](https://example.com)\n"
    )
    _well_formed(storage)
    assert storage.startswith("<h1>About me</h1>\n<p>I work on <strong>uploads</strong> &amp;")
    for fragment in (
        "<br />",
        "<li><s>two</s></li>",
        "<ol>",
        "<blockquote>",
        "<hr />",
        "<td><code>x&lt;y</code></td>",
        '<a href="https://example.com">site</a>',
    ):
        assert fragment in storage


@pytest.mark.parametrize(
    ("info", "language"),
    [
        ("python", "py"),
        ("Python extra words", "py"),
        ("sql", "sql"),
        ("yaml", "yml"),
        ("brainfuck", None),
        ("", None),
    ],
)
def test_fenced_code_becomes_the_code_macro(info: str, language: str | None) -> None:
    storage = markdown_to_storage(f"```{info}\nx = 1\n```\n")
    _well_formed(storage)
    parameter = f'<ac:parameter ac:name="language">{language}</ac:parameter>' if language else ""
    assert storage == (
        f'<ac:structured-macro ac:name="code">{parameter}'
        "<ac:plain-text-body><![CDATA[x = 1]]></ac:plain-text-body></ac:structured-macro>"
    )


def test_code_is_kept_exactly_even_with_cdata_terminators() -> None:
    storage = markdown_to_storage("    a ]]> b <c> & d\n")
    _well_formed(storage)
    root = ET.fromstring(f"<root {_NAMESPACES}>{storage}</root>")
    body = root.find(".//{urn:ac}plain-text-body")
    assert body is not None and body.text == "a ]]> b <c> & d"


def test_images_become_ac_image() -> None:
    storage = markdown_to_storage('![Me & *you*](https://x.example/a.png?s=1&t=2 "Hi")')
    _well_formed(storage)
    assert storage == (
        '<p><ac:image ac:alt="Me &amp; you" ac:title="Hi">'
        '<ri:url ri:value="https://x.example/a.png?s=1&amp;t=2" /></ac:image></p>'
    )
    assert "ac:title" not in markdown_to_storage("![a](https://x.example/a.png)")


def test_raw_html_is_shown_as_text() -> None:
    storage = markdown_to_storage("<script>alert(1)</script>")
    assert "<script>" not in storage
    assert "&lt;script&gt;" in storage
