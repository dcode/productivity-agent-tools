"""Reading Jira's field metadata and matching values against it.

Which fields an issue can be edited or created with, and which values they
accept, depend on the project's screens and schemes. Jira describes them in
``editmeta`` (one issue) and ``createmeta`` (one project and issue type).
Write tools check every field against that metadata before sending, so a
field that is not on the screen, or a value that is not allowed, is refused
with the choices listed instead of failing on the server.
"""

from dataclasses import dataclass
from datetime import date

from pydantic import JsonValue

from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.jira.models import FieldInfo
from productivity_agent_tools.payload import JsonObject, objects, text, value

MAX_LISTED_VALUES: int = 50
# Fields Jira fills in itself on create, so a "required" flag on them never needs input.
_FILLED_ON_CREATE: frozenset[str] = frozenset({"project", "issuetype", "reporter"})
_CLEAR: frozenset[str] = frozenset({"", "none", "clear", "null"})


@dataclass(frozen=True, slots=True)
class FieldMeta:
    """What Jira says about one field on an edit or create screen.

    Attributes:
        id: Field id, such as ``priority`` or ``customfield_10010``.
        name: Field name as shown in Jira.
        required: True if the screen requires a value.
        has_default: True if Jira fills in a default when none is given.
        type: Schema type, such as ``string``, ``array`` or ``priority``.
        items: For an array, the type of its items.
        allowed: The values the field accepts, if it lists them.
        operations: How the field can be changed, such as ``set``, ``add`` or ``remove``.
    """

    id: str
    name: str
    required: bool
    has_default: bool
    type: str | None
    items: str | None
    allowed: tuple[JsonObject, ...]
    operations: tuple[str, ...]

    def info(self) -> FieldInfo:
        """Describe the field for the agent.

        Returns:
            The field with up to 50 allowed values by name.
        """
        names = [_label(item) for item in self.allowed]
        return FieldInfo(
            id=self.id,
            name=self.name,
            required=self.required and not self.has_default,
            type=f"{self.type} of {self.items}"
            if self.type == "array" and self.items
            else self.type,
            allowed_values=names[:MAX_LISTED_VALUES],
            more_values=max(0, len(names) - MAX_LISTED_VALUES),
            operations=list(self.operations),
        )


def _label(item: JsonObject) -> str:
    """Pick the human name of an allowed value.

    Args:
        item: One allowed value as Jira lists it.

    Returns:
        Its name, value or id, whichever is present first.
    """
    return text(item, "name") or text(item, "value") or text(item, "key") or text(item, "id") or ""


def _field(field_id: str, raw: JsonObject) -> FieldMeta:
    """Build one field's metadata from Jira's description of it.

    Args:
        field_id: The field id.
        raw: Jira's object for the field.

    Returns:
        The metadata.
    """
    operations = value(raw, "operations")
    return FieldMeta(
        id=field_id,
        name=text(raw, "name") or field_id,
        required=raw.get("required") is True,
        has_default=raw.get("hasDefaultValue") is True,
        type=text(raw, "schema", "type"),
        items=text(raw, "schema", "items"),
        allowed=tuple(objects(raw.get("allowedValues"))),
        operations=tuple(op for op in operations if isinstance(op, str))
        if isinstance(operations, list)
        else (),
    )


def parse_editmeta(body: JsonValue) -> dict[str, FieldMeta]:
    """Read the fields of an ``editmeta`` response.

    Args:
        body: Decoded response: ``{"fields": {id: {...}}}``.

    Returns:
        The fields by id.
    """
    fields = value(body, "fields")
    if not isinstance(fields, dict):
        return {}
    return {fid: _field(fid, raw) for fid, raw in fields.items() if isinstance(raw, dict)}


def page_values(body: JsonValue) -> list[JsonObject]:
    """Read the items of a ``createmeta`` page, which older servers send as a bare list.

    Args:
        body: Decoded response.

    Returns:
        The items.
    """
    return objects(body) if isinstance(body, list) else objects(value(body, "values"))


def parse_createmeta(body: JsonValue) -> dict[str, FieldMeta]:
    """Read the fields of a ``createmeta`` issue-type response.

    Args:
        body: Decoded response: a page of field objects that each carry ``fieldId``.

    Returns:
        The fields by id.
    """
    return {
        fid: _field(fid, raw)
        for raw in page_values(body)
        if (fid := text(raw, "fieldId")) is not None
    }


def missing_required(fields: dict[str, FieldMeta], given: set[str]) -> list[FieldMeta]:
    """List the required create fields that were not given and have no default.

    Args:
        fields: The create screen's fields.
        given: Ids of the fields the request sets.

    Returns:
        The fields still needed.
    """
    return [
        meta
        for fid, meta in fields.items()
        if meta.required
        and not meta.has_default
        and fid not in given
        and fid not in _FILLED_ON_CREATE
    ]


def require(fields: dict[str, FieldMeta], field_id: str, where: str) -> FieldMeta:
    """Get a field's metadata, refusing a field the screen does not have.

    Args:
        fields: The screen's fields.
        field_id: The field wanted.
        where: The screen, for the message, such as "the edit screen of ABC-1".

    Returns:
        The field's metadata.

    Raises:
        AgentToolsError: If the screen does not have the field.
    """
    meta = fields.get(field_id)
    if meta is None:
        raise AgentToolsError(
            f"'{field_id}' is not on {where}, so Jira will not accept it there. "
            "Tell the user; a Jira administrator decides which fields a screen shows."
        )
    return meta


def choose(meta: FieldMeta, wanted: str) -> JsonObject:
    """Match a value by name against a field's allowed values.

    Args:
        meta: The field, with its allowed values.
        wanted: The value's name (any letter case) or id.

    Returns:
        A reference to send, such as ``{"id": "3"}``.

    Raises:
        AgentToolsError: If no allowed value matches.
    """
    folded = wanted.strip().casefold()
    for item in meta.allowed:
        names = {_label(item).casefold(), (text(item, "id") or "").casefold()}
        if folded in names:
            ref = text(item, "id")
            return {"id": ref} if ref else {"name": _label(item)}
    listed = ", ".join(_label(item) for item in meta.allowed[:MAX_LISTED_VALUES]) or "none"
    raise AgentToolsError(f"'{wanted}' is not a valid {meta.name}. Allowed: {listed}.")


def check_label(label: str) -> str:
    """Validate one label, which Jira stores as a single word.

    Args:
        label: The label as given.

    Returns:
        The label without surrounding whitespace.

    Raises:
        AgentToolsError: If it is empty or contains whitespace.
    """
    cleaned = label.strip()
    if not cleaned or any(ch.isspace() for ch in cleaned):
        raise AgentToolsError(
            f"'{label}' is not a valid label: labels are single words without spaces, "
            "such as needs-review."
        )
    return cleaned


def due_date(given: str) -> str | None:
    """Turn a due date into what Jira takes, or None to clear it.

    Args:
        given: An ISO date such as 2026-11-30, or "none" to clear.

    Returns:
        The date as ``YYYY-MM-DD``, or None.

    Raises:
        AgentToolsError: If it is not a date.
    """
    cleaned = given.strip()
    if cleaned.casefold() in _CLEAR:
        return None
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError:
        raise AgentToolsError(f"'{given}' is not a date such as 2026-11-30.") from None
