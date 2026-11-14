"""Settings for the ServiceNow product, read from the ``[servicenow]`` config table."""

from dataclasses import dataclass
from typing import Any

from productivity_agent_tools.config import ProductSettings, as_str, product_overrides
from productivity_agent_tools.errors import ConfigError


@dataclass(frozen=True, slots=True, kw_only=True)
class ServiceNowSettings(ProductSettings):
    """Settings for the ServiceNow module, which is on only when ``[servicenow]`` is configured.

    Attributes:
        browser: Browser that ``login servicenow`` starts: a path or a command name. None
            looks for Chrome, then Edge.
        login_timeout_seconds: How long ``login servicenow`` waits for the sign-in to finish.
    """

    browser: str | None = None
    login_timeout_seconds: float = 300.0


def parse_servicenow_settings(raw: dict[str, Any]) -> ServiceNowSettings:
    """Build ServiceNow settings from the ``[servicenow]`` table.

    Args:
        raw: Parsed ``[servicenow]`` table (possibly empty).

    Returns:
        The ServiceNow settings.

    Raises:
        ConfigError: If a value has the wrong type.
    """
    timeout = raw.get("login_timeout_seconds", ServiceNowSettings().login_timeout_seconds)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0:
        raise ConfigError(
            "Config key 'servicenow.login_timeout_seconds' must be a positive number."
        )
    return ServiceNowSettings(
        browser=(as_str(raw, "browser", None) or "").strip() or None,
        login_timeout_seconds=float(timeout),
        **product_overrides(raw, "servicenow"),
    )
