"""This computer's time zone, as a real IANA zone where the system names one.

``datetime.now()`` with no zone gives a naive local time: right for the date, but
it says nothing about which zone it is in and cannot be compared with the UTC
times the services return. This finds the zone itself, so times can be aware.

Where it looks, in order: the ``TZ`` environment variable; the target of
``/etc/localtime``, which on macOS and Linux is a link into a ``zoneinfo``
folder whose path after ``zoneinfo/`` is the zone's name; and last, the fixed UTC
offset the system reports now. Callers look it up on every use rather than once,
so a long-running server follows a laptop that changes time zone, and the
fallback's fixed offset is never more than one call out of date.
"""

import datetime
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCALTIME: str = "/etc/localtime"
_MARKER = "zoneinfo/"


def _name_from_path(path: str) -> str | None:
    """Read a zone's name from a path into a zoneinfo folder.

    Args:
        path: A path such as ``/usr/share/zoneinfo/America/New_York``.

    Returns:
        The name after ``zoneinfo/``, or None when the path has none.
    """
    _, found, name = path.partition(_MARKER)
    return (name.strip("/") or None) if found else None


def local_zone_name() -> str | None:
    """Find the IANA name of this computer's time zone.

    Returns:
        The name, such as ``America/New_York``, or None when the system does not say.
    """
    setting = os.environ.get("TZ", "").strip().removeprefix(":")
    if setting:
        return _name_from_path(setting) if setting.startswith("/") else setting
    return _name_from_path(os.path.realpath(LOCALTIME))


def local_zone() -> datetime.tzinfo:
    """This computer's time zone.

    Returns:
        The IANA zone when the system names one that is installed, else the fixed UTC
        offset the system reports now.
    """
    name = local_zone_name()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            pass
    zone = datetime.datetime.now().astimezone().tzinfo
    assert zone is not None  # astimezone() always gives an aware time
    return zone


def zone_label(zone: datetime.tzinfo) -> str:
    """Name a time zone for people: its IANA name, else its current abbreviation.

    Args:
        zone: The zone.

    Returns:
        The label, such as ``America/New_York`` or ``EST``.
    """
    if isinstance(zone, ZoneInfo):
        return zone.key
    return zone.tzname(datetime.datetime.now(zone)) or str(zone)
