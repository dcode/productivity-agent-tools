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

_SYS_ID = re.compile(r"^[0-9a-f]{32}$")
# Catalog variable types answered by picking a record from a table.
# (A lookup select box is not one: the API lists its choices like any select box.)
LOOKUP_KINDS: frozenset[str] = frozenset({"reference", "8"})
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
        # As the portal names them: a formatter, a group of checkboxes' frame, an
        # attachment drop (which the API cannot fill).
        "formatter",
        "checkbox_container",
        "sc_attachment",
        "widget",
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
        lookup_table: For a question answered by picking a record (a reference, such as a
            site), the table it is picked from; its answer is that record's ``sys_id``.
    """

    name: str
    label: str
    kind: str
    mandatory: bool
    choices: tuple[FormChoice, ...] = ()
    default: str = ""
    help_text: str = ""
    lookup_table: str = ""

    def choose(self, answer: str) -> str:
        """Turn an answer into the value to send, matching a choice by value or label.

        Args:
            answer: The answer as written.

        Returns:
            The choice's value, or the answer as written for a free-text question.

        Raises:
            AgentToolsError: If the question offers choices and none matches, or it is a
                lookup and the answer is not a sys_id.
        """
        written = answer.strip()
        if self.lookup_table and not _SYS_ID.match(written):
            raise AgentToolsError(
                f"'{self.label}' is picked from a table ({self.lookup_table}), so its answer "
                f"is the chosen record's sys_id (32 hex characters). No single record named "
                f"'{written}' was found there (or the table cannot be read), so ask the user "
                "for the sys_id."
            )
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
        source: Where the form was read: ``"portal page"`` or ``"catalog API"``.
    """

    sys_id: str
    name: str
    description: str = ""
    questions: tuple[Question, ...] = field(default=())
    source: str = "catalog API"

    def find(self, key: str) -> Question | None:
        """Find a question as ``question`` does, or None when there is none.

        Args:
            key: A variable name, a label, or a label as an identifier.

        Returns:
            The question, or None.
        """
        try:
            return self.question(key)
        except AgentToolsError:
            return None

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

    def configured_each(self, defaults: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """Resolve configured defaults one at a time, keeping those that fit the form.

        For showing the form: one default that does not fit (a question renamed, a
        choice removed) is reported instead of hiding the whole form.

        Args:
            defaults: Answers by question name or label, from the config.

        Returns:
            The values that fit, by question name, and a sentence for each that did not.
        """
        chosen: dict[str, str] = {}
        problems: list[str] = []
        for key, answer in defaults.items():
            try:
                chosen = self._chosen({key: answer}, chosen)
            except AgentToolsError as exc:
                problems.append(f"In [servicenow.incident_defaults]: {exc}")
        return chosen, problems

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


def is_sys_id(candidate: str) -> bool:
    """Tell whether a string is a record's ``sys_id`` (32 lowercase hex characters)."""
    return bool(_SYS_ID.match(candidate))


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
                lookup_table=(text(variable, "reference") or "an unnamed table")
                if kind in LOOKUP_KINDS
                else "",
            )
        )
    return CatalogForm(
        sys_id=text(item, "sys_id") or "",
        name=text(item, "name") or "",
        description=text(item, "short_description") or "",
        questions=tuple(questions),
    )


def _portal_question(field: JsonObject) -> Question | None:
    """Read one question from the portal's form model, or None for layout or hidden ones.

    Args:
        field: One entry of the portal's ``sc_cat_item._fields``.

    Returns:
        The question, or None.
    """
    name = text(field, "name")
    kind = _kind(field)
    if not name or kind in LAYOUT_KINDS or value(field, "visible") is False:
        return None
    return Question(
        name=name,
        label=text(field, "label") or name,
        kind=kind,
        mandatory=_flag(value(field, "mandatory")),
        choices=_choices(field),
        default=_string(value(field, "value")),
        help_text=text(field, "help_text") or text(field, "instructions") or "",
        lookup_table=(
            text(field, "ed", "reference") or text(field, "refTable") or "an unnamed table"
        )
        if kind in LOOKUP_KINDS
        else "",
    )


def _find_catalog_item(source: JsonValue) -> JsonObject | None:
    """Find the form model (an object with ``_fields``) anywhere in a portal page answer."""
    if isinstance(source, dict):
        item = source.get("sc_cat_item")
        if isinstance(item, dict) and isinstance(item.get("_fields"), dict | list):
            return item
        children = list(source.values())
    elif isinstance(source, list):
        children = source
    else:
        return None
    for child in children:
        if (found := _find_catalog_item(child)) is not None:
            return found
    return None


def _laid_out(sections: JsonValue) -> list[str]:
    """List field names in the order the portal's form lays them out.

    Args:
        sections: The item's ``_sections``: containers of columns of fields.

    Returns:
        The names, section by section and column by column.
    """
    return [
        name
        for section in objects(sections)
        for column in objects(value(section, "columns"))
        for field in objects(value(column, "fields"))
        if (name := text(field, "name"))
    ]


def parse_portal_form(page: JsonValue) -> CatalogForm | None:
    """Read a catalog item's form from the portal's page answer, as the portal shows it.

    The Service Catalog API can leave out questions the portal shows (seen on the
    work instance: only one of eleven came back), so the form is read the way the
    portal reads it: from ``GET /api/now/sp/page?id=sc_cat_item&sys_id=...``, whose
    catalog-item widget carries the whole form model.

    Args:
        page: The decoded page answer.

    Returns:
        The form, or None when the page holds no catalog item.
    """
    item = _find_catalog_item(page)
    if item is None:
        return None
    raw = item["_fields"]
    fields = objects(list(raw.values()) if isinstance(raw, dict) else raw)
    questions = [q for field in fields if (q := _portal_question(field)) is not None]
    # _fields is keyed by name in no set order; the form's layout gives the order.
    order = {name: place for place, name in enumerate(_laid_out(value(item, "_sections")))}
    questions.sort(key=lambda question: order.get(question.name, len(order)))
    return CatalogForm(
        sys_id=text(item, "sys_id") or "",
        name=text(item, "name") or "",
        description=text(item, "short_description") or "",
        questions=tuple(questions),
    )
