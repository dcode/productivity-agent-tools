#!/usr/bin/env python3
"""Build the `replacements` for a Confluence profile page made from the team template.

Usage:
    python3 fill_profile.py page.json > replacements.json

page.json is the output of:
    productivity-agent-tools call confluence_get_page page_id=<id> max_chars=5000

The text to find is taken from that file exactly as the server sent it, so nothing
is retyped. Fill in ANSWERS below first. Leave an answer as "" to keep its grey hint.
Write plain text: &, < and > are escaped for you.
"""

import html
import json
import re
import sys

ANSWERS = {
    "Location / time zone": "",
    "Usually on-site": "",
    "With Cloud Ops since": "",
    "Best way to reach me": "",
    "Ask me about": "",
    "What I work on": [],  # one string per bullet
    "How I got into tech": "",
    "Outside of work": "",
    "Food": "",
    "Movie": "",
    "Animal": "",
    "Season": "",
    "Fun fact": "",
    "How I like to work": "",
}
REMOVE_TODO_BOX = True

# The template's hints, in page order. The photo hint is left alone.
ORDER = [
    "Photo",
    "Location / time zone",
    "Usually on-site",
    "With Cloud Ops since",
    "Best way to reach me",
    "Ask me about",
    "work-1",
    "work-2",
    "How I got into tech",
    "Outside of work",
    "Food",
    "Movie",
    "Animal",
    "Season",
    "Fun fact",
    "How I like to work",
]
HINT = re.compile(r"<ac:placeholder>.*?</ac:placeholder>", re.S)
TODO = re.compile(r'<ac:structured-macro ac:name="note".*?</ac:structured-macro>', re.S)


def unique_from(body: str, start: int, end: int) -> int:
    """Move start left until body[start:end] occurs exactly once."""
    while body.count(body[start:end]) > 1:
        start -= 1
    return start


def main() -> None:
    """This is the entrypoint. No args. No return."""
    page = json.load(open(sys.argv[1], encoding="utf-8"))
    body = page["body"]
    if page.get("truncated"):
        sys.exit("The body was cut short: read the page again with a larger max_chars.")
    hints = list(HINT.finditer(body))
    if len(hints) != len(ORDER):
        sys.exit(f"Expected {len(ORDER)} hints but the page has {len(hints)}; it has changed.")

    bullets = [b.strip() for b in ANSWERS["What I work on"] if b.strip()]
    texts = {k: v.strip() for k, v in ANSWERS.items() if isinstance(v, str)}
    out = []
    for name, hint in zip(ORDER, hints, strict=True):
        start, end = hint.span()
        if name == "work-1":
            new = html.escape(bullets[0], quote=False) if bullets else ""
        elif name == "work-2":
            if not bullets:
                continue
            if len(bullets) == 1:  # drop the unused second bullet, <li> and all
                start, end = start - len("<li>"), end + len("</li>")
                new = None
            else:
                new = "</li><li>".join(html.escape(b, quote=False) for b in bullets[1:])
        else:
            new = html.escape(texts.get(name, ""), quote=False)
        if new == "":
            continue
        begin = unique_from(body, start, end)
        out.append({"find": body[begin:end], "replace": body[begin:start] + (new or "")})
    todo = TODO.search(body)
    if REMOVE_TODO_BOX and todo and "To do" in todo.group(0):
        out.append({"find": todo.group(0), "replace": ""})
    if not out:
        sys.exit("Nothing to do: fill in ANSWERS at the top of this script first.")
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print(f"\n{len(out)} replacements for version {page['version']}.", file=sys.stderr)


if __name__ == "__main__":
    main()
