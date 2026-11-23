"""Settings for the ServiceNow product, read from the ``[servicenow]`` config table."""

from dataclasses import dataclass, field
from typing import Any

from productivity_agent_tools.config import (
    ProductSettings,
    as_str,
    login_timeout,
    product_overrides,
)
from productivity_agent_tools.errors import ConfigError

DEFAULT_INCIDENT_FORM: str = "Create an incident"


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceNowSettings(ProductSettings):
    """Settings for the ServiceNow module, which is on only when ``[servicenow]`` is configured.

    Attributes:
        browser: Browser that ``login servicenow`` starts: a path or a command name. None
            looks for Chrome, then Edge.
        login_timeout_seconds: How long ``login servicenow`` waits for the sign-in to finish.
        incident_form: The catalog form that opens an incident (the portal's "Create an
            incident"): its exact name or its ``sys_id``.
        incident_defaults: Answers to that form's questions used when the agent gives
            none, by question name or label, from ``[servicenow.incident_defaults]``.
        order_defaults: Answers used when ordering any catalog item whose form has the
            question, from ``[servicenow.order_defaults]``; keys naming a question an
            item's form does not have are skipped for that item.
        portal: The service portal the form is read from, as its address suffix (the
            ``sp`` in ``/sp?id=...``); its ``sys_id`` is read from its page.
        portal_id: The portal's ``sys_id``, when it should not be read from its page.
    """

    browser: str | None = None
    login_timeout_seconds: float = 300.0
    incident_form: str = DEFAULT_INCIDENT_FORM
    incident_defaults: dict[str, str] = field(default_factory=dict)
    order_defaults: dict[str, str] = field(default_factory=dict)
    portal: str | None = None
    portal_id: str | None = None


def _answers(raw: dict[str, Any], table: str) -> dict[str, str]:
    """Read a table of default answers: question names or labels to strings.

    Args:
        raw: Parsed ``[servicenow]`` table.
        table: The sub-table's name, such as ``incident_defaults``.

    Returns:
        The answers by key.

    Raises:
        ConfigError: If the table is not a mapping of strings.
    """
    answers = raw.get(table, {})
    if not isinstance(answers, dict) or not all(isinstance(a, str) for a in answers.values()):
        raise ConfigError(
            f"Config table '[servicenow.{table}]' must map question names or labels to "
            "string answers."
        )
    return {str(key): answer for key, answer in answers.items()}


def parse_servicenow_settings(raw: dict[str, Any]) -> ServiceNowSettings:
    """Build ServiceNow settings from the ``[servicenow]`` table.

    Args:
        raw: Parsed ``[servicenow]`` table (possibly empty).

    Returns:
        The ServiceNow settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    default = ServiceNowSettings().login_timeout_seconds
    return ServiceNowSettings(
        browser=(as_str(raw, "browser", None) or "").strip() or None,
        login_timeout_seconds=login_timeout(raw, "servicenow", default),
        incident_form=(as_str(raw, "incident_form", None) or "").strip() or DEFAULT_INCIDENT_FORM,
        incident_defaults=_answers(raw, "incident_defaults"),
        order_defaults=_answers(raw, "order_defaults"),
        portal=(as_str(raw, "portal", None) or "").strip().strip("/") or None,
        portal_id=(as_str(raw, "portal_id", None) or "").strip() or None,
        **product_overrides(raw, "servicenow"),
    )
