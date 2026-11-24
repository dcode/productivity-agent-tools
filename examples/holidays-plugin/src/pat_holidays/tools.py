"""The MCP tools: a few typed, narrow reads that answer one question each."""

from datetime import date
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from pat_holidays.client import Holidays, describe
from productivity_agent_tools.products import ToolContext
from productivity_agent_tools.tooling import READ, anticipated


class HolidayList(BaseModel):
    """Public holidays in one country, one line each, in date order."""

    country: str = Field(description="ISO 3166-1 alpha-2 country code.")
    holidays: list[str] = Field(description="Each holiday as 'YYYY-MM-DD: name (local name)'.")


def register_holiday_tools(server: MCPServer, holidays: Holidays, context: ToolContext) -> None:
    """Add the holidays tools to a server.

    Args:
        server: The MCP server.
        holidays: The client the tools call.
        context: Run details (dry-run, peers); these tools are reads and need none.
    """
    del context

    @server.tool(annotations=READ)
    @anticipated
    async def holidays_list(
        year: Annotated[int, Field(ge=1975, le=2075)] | None = None, country: str | None = None
    ) -> HolidayList:
        """List a year's public holidays, to plan around days off.

        Args:
            year: The year; this year when left out.
            country: ISO 3166-1 alpha-2 code, such as US or DE; the user's when left out.
        """
        rows = await holidays.holidays(year or date.today().year, country)
        return HolidayList(country=holidays.country(country), holidays=[describe(r) for r in rows])

    @server.tool(annotations=READ)
    @anticipated
    async def holidays_next(
        country: str | None = None, max_results: Annotated[int, Field(ge=1, le=30)] = 5
    ) -> HolidayList:
        """List the next public holidays, soonest first.

        Args:
            country: ISO 3166-1 alpha-2 code, such as US or DE; the user's when left out.
            max_results: Most holidays to return.
        """
        rows = await holidays.upcoming(country)
        return HolidayList(
            country=holidays.country(country), holidays=[describe(r) for r in rows[:max_results]]
        )
