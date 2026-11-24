"""Talking to Nager.Date: the adapter (HTTP and errors) and the client (what to ask for).

The adapter turns HTTP answers into data or plain-language errors; the client
knows the API's paths. Keeping them apart is what lets tests swap the transport
and lets the tools stay ignorant of HTTP.
"""

import httpx
from pydantic import JsonValue

from pat_holidays.settings import HolidaySettings
from productivity_agent_tools.api import ApiClient
from productivity_agent_tools.errors import ApiError, NotFoundError
from productivity_agent_tools.payload import JsonObject, objects, text
from productivity_agent_tools.transport import HttpAdapter


class NagerAdapter(HttpAdapter):
    """``ApiClient`` adapter for the Nager.Date API: JSON in, plain-language errors out."""

    def _interpret(self, response: httpx.Response, *, as_text: bool = False) -> JsonValue:
        """Turn a response into data, or raise an error the agent can act on.

        Args:
            response: The HTTP response.
            as_text: Return the body as text instead of decoding JSON.

        Returns:
            The decoded JSON body (or text), or None for an empty response.

        Raises:
            AgentToolsError: For a status that is not a success.
        """
        if response.status_code == 404:
            raise NotFoundError("Nager.Date has no data for that; check the country code.")
        if response.status_code >= 400:
            raise ApiError(response.status_code, f"Nager.Date returned {response.status_code}.")
        if not response.content:
            return None
        body: JsonValue = response.text if as_text else response.json()
        return body


class Holidays:
    """Reads public holidays through an ``ApiClient``.

    Attributes:
        settings: The product's settings.
    """

    def __init__(self, api: ApiClient, settings: HolidaySettings) -> None:
        """Create the client.

        Args:
            api: The adapter connected to Nager.Date.
            settings: The product's settings.
        """
        self._api = api
        self.settings = settings

    def country(self, country: str | None) -> str:
        """The country to ask about: the one given, else the configured one, in upper case."""
        return (country or self.settings.country).strip().upper()

    async def countries(self) -> list[JsonObject]:
        """List the countries Nager.Date covers (``countryCode`` and ``name``)."""
        return objects(await self._api.get("/api/v3/AvailableCountries"))

    async def holidays(self, year: int, country: str | None = None) -> list[JsonObject]:
        """List one year's public holidays in a country, in date order."""
        return objects(
            await self._api.get(f"/api/v3/PublicHolidays/{year}/{self.country(country)}")
        )

    async def upcoming(self, country: str | None = None) -> list[JsonObject]:
        """List the public holidays of the next 365 days in a country."""
        return objects(await self._api.get(f"/api/v3/NextPublicHolidays/{self.country(country)}"))


def describe(row: JsonObject) -> str:
    """One holiday as a line: its date, English name and local name when it differs."""
    name, local = text(row, "name") or "?", text(row, "localName")
    also = f" ({local})" if local and local != name else ""
    return f"{text(row, 'date')}: {name}{also}"
