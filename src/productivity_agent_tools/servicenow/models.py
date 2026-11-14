"""Shapes returned by the ServiceNow tools."""

from pydantic import BaseModel, Field


class ServiceNowStatus(BaseModel):
    """Connection status reported by the servicenow_status tool."""

    base_url: str = Field(description="Address of the ServiceNow instance in use.")
    session_source: str = Field(description="Where the browser session came from, and when.")
    username: str | None = Field(default=None, description="Signed-in user's username.")
    display_name: str | None = Field(default=None, description="Signed-in user's name.")
    dry_run: bool = Field(description="True if writes are currently blocked.")
