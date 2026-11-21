"""Finding this computer's time zone."""

import datetime
from zoneinfo import ZoneInfo

import pytest

from productivity_agent_tools import timezones
from productivity_agent_tools.timezones import local_zone, local_zone_name, zone_label


@pytest.fixture
def no_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TZ", raising=False)


@pytest.mark.parametrize(
    ("setting", "name"),
    [
        ("Europe/Berlin", "Europe/Berlin"),
        (":America/Chicago", "America/Chicago"),
        ("/usr/share/zoneinfo/Asia/Tokyo", "Asia/Tokyo"),
        ("/etc/somewhere", None),
    ],
)
def test_tz_names_the_zone_first(
    monkeypatch: pytest.MonkeyPatch, setting: str, name: str | None
) -> None:
    monkeypatch.setenv("TZ", setting)
    monkeypatch.setattr(timezones.os.path, "realpath", lambda path: pytest.fail("not read"))
    assert local_zone_name() == name


@pytest.mark.parametrize(
    ("target", "name"),
    [
        ("/usr/share/zoneinfo/America/New_York", "America/New_York"),
        ("/private/var/db/timezone/zoneinfo/Europe/London", "Europe/London"),
        ("/usr/share/zoneinfo/", None),
        ("/etc/localtime", None),
    ],
)
def test_the_localtime_link_names_the_zone(
    monkeypatch: pytest.MonkeyPatch, no_tz: None, target: str, name: str | None
) -> None:
    monkeypatch.setattr(timezones.os.path, "realpath", lambda path: target)
    assert local_zone_name() == name


def test_a_named_zone_is_a_real_zone(monkeypatch: pytest.MonkeyPatch, no_tz: None) -> None:
    monkeypatch.setattr(timezones, "local_zone_name", lambda: "America/New_York")
    zone = local_zone()
    assert zone == ZoneInfo("America/New_York")
    assert zone_label(zone) == "America/New_York"


@pytest.mark.parametrize("name", [None, "Not/AZone", "../bad"])
def test_without_a_usable_name_the_current_offset_is_used(
    monkeypatch: pytest.MonkeyPatch, name: str | None
) -> None:
    monkeypatch.setattr(timezones, "local_zone_name", lambda: name)
    zone = local_zone()
    now = datetime.datetime.now(zone)
    assert now.utcoffset() == datetime.datetime.now().astimezone().utcoffset()
    assert zone_label(zone) == now.tzname()


def test_a_zone_with_no_abbreviation_is_named_by_its_text() -> None:
    class Nameless(datetime.tzinfo):
        def utcoffset(self, dt: datetime.datetime | None) -> datetime.timedelta:
            return datetime.timedelta(0)

        def tzname(self, dt: datetime.datetime | None) -> str | None:
            return None

        def dst(self, dt: datetime.datetime | None) -> datetime.timedelta:
            return datetime.timedelta(0)

        def __str__(self) -> str:
            return "nameless"

    assert zone_label(Nameless()) == "nameless"
