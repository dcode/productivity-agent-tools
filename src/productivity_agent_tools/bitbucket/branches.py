"""Naming a new branch the way Bitbucket's branching model does.

Bitbucket's "Create branch" dialog offers the model's branch types (feature,
bugfix, hotfix, release), each with a prefix such as ``feature/``, plus
"custom" for a name with no prefix. Started from a Jira issue, it proposes
``<prefix><KEY>-<summary-in-lower-case-with-dashes>``. Branches named this way
show up on the issue's Development panel, because the name carries the key.
"""

import re
from typing import Literal

from productivity_agent_tools.errors import AgentToolsError

type BranchType = Literal["feature", "bugfix", "hotfix", "release", "custom"]

# Bitbucket's defaults; a repository's own branching model can change or switch off each.
DEFAULT_PREFIXES: dict[str, str] = {
    "feature": "feature/",
    "bugfix": "bugfix/",
    "hotfix": "hotfix/",
    "release": "release/",
}
MAX_SUMMARY_CHARS: int = 40
MAX_BRANCH_CHARS: int = 100
_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
_NOT_NAME = re.compile(r"[^A-Za-z0-9._/\-]+")
# What git refuses in a ref name, beyond the characters removed above.
_BAD_REF = re.compile(r"\.\.|//|@\{|^[-/.]|[/.]$|\.lock$|/\.")


def issue_key_of(key: str) -> str:
    """Validate an issue key for use in a branch name.

    Args:
        key: The key as given, such as ``proj-12``.

    Returns:
        The upper-cased key.

    Raises:
        AgentToolsError: If it is not shaped like an issue key.
    """
    cleaned = key.strip().upper()
    if not _ISSUE_KEY.fullmatch(cleaned):
        raise AgentToolsError(
            f"'{key}' is not a valid issue key (expected something like ABC-123)."
        )
    return cleaned


def summary_slug(summary: str) -> str:
    """Turn an issue summary into the lower-case, dashed tail of a branch name.

    Args:
        summary: The issue's summary.

    Returns:
        Up to 40 characters, cut at a word where possible; empty if nothing usable is left.
    """
    slug = _NOT_SLUG.sub("-", summary.casefold().replace("'", "").replace("\u2019", "")).strip("-")
    if len(slug) <= MAX_SUMMARY_CHARS:
        return slug
    cut = slug[: MAX_SUMMARY_CHARS + 1]
    return (cut[: cut.rindex("-")] if "-" in cut else cut[:MAX_SUMMARY_CHARS]).strip("-")


def check_ref_name(name: str) -> str:
    """Validate a full branch name against git's rules.

    Args:
        name: The branch name, including any prefix.

    Returns:
        The name.

    Raises:
        AgentToolsError: If git would refuse it.
    """
    if not name or len(name) > MAX_BRANCH_CHARS or _NOT_NAME.search(name) or _BAD_REF.search(name):
        raise AgentToolsError(
            f"'{name}' is not a valid branch name. Use letters, digits, '-', '_', '.' and '/', "
            f"at most {MAX_BRANCH_CHARS} characters, with no '..', no leading or trailing '/' "
            "or '.', and not ending in '.lock'."
        )
    return name


def branch_name(
    branch_type: str,
    prefixes: dict[str, str],
    *,
    issue_key: str | None = None,
    name: str | None = None,
    summary: str | None = None,
) -> str:
    """Build the full name of a new branch.

    Args:
        branch_type: ``feature``, ``bugfix``, ``hotfix``, ``release`` or ``custom``.
        prefixes: The prefix of each branch type the repository allows.
        issue_key: Validated issue key to start the name with, if any.
        name: The name after the prefix; spaces become dashes, and the issue key is put
            in front unless it is already there. For ``custom`` it is the whole name, used
            as given. Left out, the name is the issue key and the summary.
        summary: The issue's summary, used when ``name`` is left out.

    Returns:
        The validated branch name.

    Raises:
        AgentToolsError: If the type is unknown or switched off, there is nothing to name
            the branch after, or the result is not a valid branch name.
    """
    kind = branch_type.strip().casefold()
    if kind != "custom" and kind not in prefixes:
        allowed = ", ".join([*prefixes, "custom"])
        raise AgentToolsError(
            f"'{branch_type}' is not a branch type this repository allows. Use one of: {allowed}."
        )
    prefix = "" if kind == "custom" else prefixes[kind]
    given = _NOT_NAME.sub("-", name.strip()).strip("-") if name and name.strip() else ""
    if given:
        given = given.removeprefix(prefix) if prefix else given
        with_key = issue_key and prefix and issue_key.casefold() not in given.casefold()
        tail = f"{issue_key}-{given}" if with_key else given
    elif issue_key:
        slug = summary_slug(summary or "")
        tail = f"{issue_key}-{slug}" if slug else issue_key
    else:
        raise AgentToolsError("Give an issue_key, a name, or both, to name the branch after.")
    return check_ref_name(f"{prefix}{tail}")
