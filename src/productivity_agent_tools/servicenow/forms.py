"""Service Catalog forms: their questions, and checking answers against them.

The portal's "Create an incident" page is a record producer: a catalog item
whose questions (variables) a script turns into the incident. Its required
questions are not incident fields (a site, a network connection), so an
incident is opened by submitting the form, as the portal does, never by writing
the incident table directly. This module reads a form's definition from the
Service Catalog API and checks a set of answers against it before anything is
sent: every required question answered, every choice one the form offers.
"""

import re
from dataclasses import dataclass, field

from pydantic import JsonValue

from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.payload import JsonObject, objects, text, value

# Catalog variable types that only lay the form out and take no answer, by the
# Service Catalog API's friendly name and by number.
LAYOUT_KINDS: frozenset[str] = frozenset(
    {
        "label",
        "break",
        "container_start",
        "container_end",
        "container_split",
        "macro",
        "macro_with_label",
        "ui_page",
        "rich_text_label",
        "11",
        "12",
        "14",
        "15",
        "17",
        "19",
        "20",
        "24",
        "32",
    }
)


@dataclass(frozen=True, slots=True)
class FormChoice:
    """One choice a question offers.

    Attributes:
        value: What is sent.
        label: What the form shows.
    """

    value: str
    label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Question:
    """One question on a catalog form.

    Attributes:
        name: The variable's name, which the submission is keyed by.
        label: The question as the form shows it.
        kind: The variable type (``select_box``, ``multi_line_text`` ...).
        mandatory: True if the form requires an answer.
        choices: The choices, for a question that offers them; empty otherwise.
        default: The form's own default answer, if any.
        help_text: The form's help text, if any.
    """

    name: str
    label: str
    kind: str
    mandatory: bool
    choices: tuple[FormChoice, ...] = ()
    default: str = ""
    help_text: str = ""

    def choose(self, answer: str) -> str:
        """Turn an answer into the value to send, matching a choice by value or label.

        Args:
            answer: The answer as written.

        Returns:
            The choice's value, or the answer as written for a free-text question.

        Raises:
            AgentToolsError: If the question offers choices and none matches.
        """
        written = answer.strip()
        if not self.choices:
            return written
        folded = written.casefold()
        for choice in self.choices:
            if folded in (choice.value.casefold(), choice.label.casefold()):
                return choice.value
        offered = "; ".join(choice.label or choice.value for choice in self.choices)
        raise AgentToolsError(
            f"'{written}' is not a choice for '{self.label}'. Choices: {offered}."
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CatalogForm:
    """A catalog item and the questions on its form.

    Attributes:
        sys_id: The catalog item's ``sys_id``, which the submission is sent to.
        name: The item's name, such as "Create an incident".
        description: The item's short description.
        questions: The questions that take an answer, in the form's order.
    """

    sys_id: str
    name: str
    description: str = ""
    questions: tuple[Question, ...] = field(default=())

    def question(self, key: str) -> Question:
        """Find a question by its name, or by its label in any case or written as an identifier.

        In order: the exact name; the label or name in any case; then the label or
        name in snake_case or camelCase, so ``network_connection`` and
        ``networkConnection`` both find "Network Connection", and a config file needs
        no quoted keys.

        Args:
            key: A variable name, a label, or a label as an identifier.

        Returns:
            The question.

        Raises:
            AgentToolsError: If no question has that name or label.
        """
        wanted = key.strip()
        for question in self.questions:
            if question.name == wanted:
                return question
        folded = _plain(wanted)
        for question in self.questions:
            if _plain(question.label) == folded or question.name.casefold() == folded:
                return question
        slug = _slug(wanted)
        for question in self.questions:
            if slug and slug in (_slug(question.label), _slug(question.name)):
                return question
        names = ", ".join(q.name for q in self.questions)
        raise AgentToolsError(f"'{wanted}' is not a question on '{self.name}'. Questions: {names}.")

    def answers(
        self, given: dict[str, str], defaults: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Check answers against the form and build the variables to submit.

        Each question takes the answer given, else the configured default, else the
        form's own default.

        Args:
            given: Answers by question name or label, from the agent.
            defaults: Answers by question name or label, from the config.

        Returns:
            The values to send, by variable name, in the form's order.

        Raises:
            AgentToolsError: If an answer names no question, a choice is not offered,
                or a required question is left unanswered.
        """
        chosen = self._chosen(given, self.configured(defaults or {}))
        variables: dict[str, str] = {}
        missing: list[str] = []
        for question in self.questions:
            answer = chosen.get(question.name) or question.default
            if answer:
                variables[question.name] = answer
            elif question.mandatory:
                missing.append(f"{question.name} ({question.label})")
        if missing:
            raise AgentToolsError(
                f"'{self.name}' requires answers to: {'; '.join(missing)}. "
                "servicenow_get_incident_form lists each question with its choices. "
                "Nothing was sent."
            )
        return variables

    def configured(self, defaults: dict[str, str]) -> dict[str, str]:
        """Resolve configured default answers to the values they send, by question name.

        Args:
            defaults: Answers by question name or label, from the config.

        Returns:
            The values by question name.

        Raises:
            AgentToolsError: If a default names no question or offers no listed choice.
        """
        try:
            return self._chosen(defaults, {})
        except AgentToolsError as exc:
            raise AgentToolsError(f"In [servicenow.incident_defaults]: {exc}") from exc

    def _chosen(self, source: dict[str, str], chosen: dict[str, str]) -> dict[str, str]:
        """Apply answers over earlier ones; an empty answer clears an earlier one.

        Args:
            source: Answers by question name or label.
            chosen: Earlier values by question name.

        Returns:
            The combined values by question name.
        """
        combined = dict(chosen)
        for key, answer in source.items():
            question = self.question(key)
            if answer.strip():
                combined[question.name] = question.choose(answer)
            else:
                combined.pop(question.name, None)
        return combined


def _slug(words: str) -> str:
    """Write a label or key as a snake_case identifier: "What's the impact?" -> whats_the_impact.

    camelCase is split first, so ``networkConnection`` becomes ``network_connection``.
    """
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", words)
    return re.sub(r"[^a-z0-9]+", "_", split.replace("'", "").casefold()).strip("_")


def _plain(label: str) -> str:
    """Fold a label for matching: any case, and without trailing punctuation or spaces."""
    return label.strip().rstrip(":?*").strip().casefold()


def _flag(raw: JsonValue) -> bool:
    """Read a flag the API may send as a boolean or as the string ``"true"``."""
    return raw is True or (isinstance(raw, str) and raw.strip().casefold() == "true")


def _string(raw: JsonValue) -> str:
    """Read a value the API may send as a string or a number; anything else is empty."""
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, str | int | float):
        return str(raw)
    return ""


def _kind(variable: JsonObject) -> str:
    """Name a variable's type, preferring the API's friendly name."""
    return text(variable, "friendly_type") or _string(value(variable, "type"))


def _choices(variable: JsonObject) -> tuple[FormChoice, ...]:
    """Read a variable's choices, skipping the empty "-- None --" placeholder."""
    found: list[FormChoice] = []
    for choice in objects(value(variable, "choices")):
        choice_value = _string(value(choice, "value"))
        if choice_value:
            found.append(FormChoice(choice_value, text(choice, "label") or choice_value))
    return tuple(found)


def parse_form(item: JsonObject) -> CatalogForm:
    """Read a catalog item's form from the Service Catalog API's item answer.

    Args:
        item: The ``result`` of ``GET /api/sn_sc/servicecatalog/items/{sys_id}``.

    Returns:
        The form, with layout-only variables left out.
    """
    questions: list[Question] = []
    for variable in objects(value(item, "variables")):
        name = text(variable, "name")
        kind = _kind(variable)
        if not name or kind in LAYOUT_KINDS:
            continue
        questions.append(
            Question(
                name=name,
                label=text(variable, "label") or name,
                kind=kind,
                mandatory=_flag(value(variable, "mandatory")),
                choices=_choices(variable),
                default=_string(value(variable, "value")),
                help_text=text(variable, "help_text") or "",
            )
        )
    return CatalogForm(
        sys_id=text(item, "sys_id") or "",
        name=text(item, "name") or "",
        description=text(item, "short_description") or "",
        questions=tuple(questions),
    )
