"""Helpers for reading decoded JSON from an API without raising on odd shapes."""

from pydantic import JsonValue

from productivity_agent_tools.errors import ApiError

type JsonObject = dict[str, JsonValue]


def text(source: JsonValue, *keys: str) -> str | None:
    """Read a nested string out of decoded JSON without raising.

    Args:
        source: Decoded JSON value to walk.
        *keys: Successive object keys to follow.

    Returns:
        The string found at the end of the path, or None.
    """
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def number(source: JsonValue, *keys: str) -> int | None:
    """Read a nested integer out of decoded JSON without raising.

    Args:
        source: Decoded JSON value to walk.
        *keys: Successive object keys to follow.

    Returns:
        The integer found at the end of the path, or None.
    """
    current = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, int) and not isinstance(current, bool) else None


def objects(source: JsonValue) -> list[JsonObject]:
    """Return the JSON objects inside a decoded JSON array.

    Args:
        source: Decoded JSON value expected to be an array.

    Returns:
        The array's object members; empty if the value is not an array.
    """
    if not isinstance(source, list):
        return []
    return [item for item in source if isinstance(item, dict)]


def as_object(source: JsonValue, what: str) -> JsonObject:
    """Require a decoded JSON value to be an object.

    Args:
        source: Decoded JSON value.
        what: Name of the response, used in the error message.

    Returns:
        The value as an object.

    Raises:
        ApiError: If the value is not an object.
    """
    if not isinstance(source, dict):
        raise ApiError(200, f"The server returned an unexpected {what} response.")
    return source
