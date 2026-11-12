"""Turn Markdown into Confluence storage format.

Agents write Markdown fluently, while Confluence stores pages as XHTML with
its own ``ac:`` and ``ri:`` elements. Confluence 10 can no longer convert wiki
markup through its REST API, so the conversion happens here.

CommonMark plus tables and strikethrough is rendered as XHTML, which is valid
storage format, with two changes: fenced code becomes Confluence's code macro
(with its language when one is given), and images become ``ac:image``. Raw
HTML in the Markdown is shown as text rather than passed through, so a page
cannot be broken by stray markup; send storage format directly for macros.
"""

import html
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

# Languages Confluence's code macro knows; anything else is shown without highlighting.
_CODE_LANGUAGES: frozenset[str] = frozenset(
    {
        "actionscript3",
        "applescript",
        "bash",
        "c#",
        "cpp",
        "css",
        "coldfusion",
        "delphi",
        "diff",
        "erl",
        "groovy",
        "xml",
        "java",
        "jfx",
        "js",
        "php",
        "perl",
        "text",
        "powershell",
        "py",
        "ruby",
        "sql",
        "sass",
        "scala",
        "vb",
        "yml",
    }
)
_LANGUAGE_ALIASES: dict[str, str] = {
    "c": "cpp",
    "c++": "cpp",
    "csharp": "c#",
    "cs": "c#",
    "go": "text",
    "html": "xml",
    "javascript": "js",
    "json": "js",
    "jsx": "js",
    "python": "py",
    "rb": "ruby",
    "sh": "bash",
    "shell": "bash",
    "ts": "js",
    "typescript": "js",
    "yaml": "yml",
    "zsh": "bash",
}


def _cdata(text: str) -> str:
    """Wrap text in CDATA, splitting any ``]]>`` it contains.

    Args:
        text: The raw text.

    Returns:
        One or more CDATA sections holding the text exactly.
    """
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _language(info: str) -> str | None:
    """Map a fence's info string to a language the code macro knows.

    Args:
        info: Text after the opening fence, such as ``python``.

    Returns:
        The macro's language name, or None when there is none to give.
    """
    word = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
    language = _LANGUAGE_ALIASES.get(word, word)
    return language if language in _CODE_LANGUAGES else None


def _code_macro(code: str, language: str | None) -> str:
    """Build Confluence's code macro.

    Args:
        code: The code, exactly as written.
        language: The macro's language, or None.

    Returns:
        The macro in storage format.
    """
    parameter = (
        f'<ac:parameter ac:name="language">{html.escape(language)}</ac:parameter>'
        if language
        else ""
    )
    return (
        f'<ac:structured-macro ac:name="code">{parameter}'
        f"<ac:plain-text-body>{_cdata(code.removesuffix(chr(10)))}</ac:plain-text-body>"
        "</ac:structured-macro>\n"
    )


def _fence(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """Render a fenced code block as the code macro.

    Args:
        self: The renderer.
        tokens: The token stream.
        idx: Index of the fence token.
        options: Renderer options.
        env: Render environment.

    Returns:
        The macro.
    """
    token = tokens[idx]
    return _code_macro(token.content, _language(token.info))


def _code_block(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """Render an indented code block as the code macro.

    Args:
        self: The renderer.
        tokens: The token stream.
        idx: Index of the code block token.
        options: Renderer options.
        env: Render environment.

    Returns:
        The macro.
    """
    return _code_macro(tokens[idx].content, None)


def _image(
    self: RendererHTML, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    """Render an image as ``ac:image`` pointing at its URL.

    Args:
        self: The renderer.
        tokens: The token stream.
        idx: Index of the image token.
        options: Renderer options.
        env: Render environment.

    Returns:
        The image in storage format.
    """
    token = tokens[idx]
    source = html.escape(str(token.attrGet("src") or ""))
    alt = html.escape(self.renderInlineAsText(token.children or [], options, env))
    title = token.attrGet("title")
    title_attribute = f' ac:title="{html.escape(str(title))}"' if title else ""
    return f'<ac:image ac:alt="{alt}"{title_attribute}><ri:url ri:value="{source}" /></ac:image>'


def _parser() -> MarkdownIt:
    """Build the Markdown parser and storage-format renderer.

    Returns:
        The configured parser.
    """
    parser = MarkdownIt("commonmark", {"xhtmlOut": True, "html": False}).enable(
        ["table", "strikethrough"]
    )
    parser.add_render_rule("fence", _fence)
    parser.add_render_rule("code_block", _code_block)
    parser.add_render_rule("image", _image)
    return parser


_PARSER: MarkdownIt = _parser()


def markdown_to_storage(markdown: str) -> str:
    """Convert Markdown to Confluence storage format.

    Args:
        markdown: The Markdown text.

    Returns:
        The page body in storage format.
    """
    return _PARSER.render(markdown).strip()
