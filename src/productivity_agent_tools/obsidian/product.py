"""The Obsidian product, as the server, configuration and CLI find it."""

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.obsidian.layout import (
    FROM_CONFIG,
    daily_layout,
    describe_zone,
    meeting_example,
    today,
)
from productivity_agent_tools.obsidian.settings import ObsidianSettings, parse_obsidian_settings
from productivity_agent_tools.obsidian.tools import register_obsidian_tools
from productivity_agent_tools.obsidian.vault import Vault
from productivity_agent_tools.products import LocalProduct

INSTRUCTIONS: str = (
    "The obsidian_ tools work in the user's Obsidian vault, where they keep task notes "
    "(TaskNotes), meeting notes and daily notes. Task notes carry a source key such as "
    "jira: ABC-123. Before making a task, call obsidian_find_task with its key, its title "
    "or both: a task with the same key already exists and must not be duplicated, and a "
    "similar title should be shown to the user to decide. To plan a day, set scheduled on "
    "the chosen tasks with obsidian_update_task and write the plan with "
    "obsidian_write_section (section plan, day today); writing a section again replaces "
    "it. Never copy a task's text into a daily note: link it as [[path without .md]]. "
    "obsidian_create_task, obsidian_update_task and obsidian_write_section change the "
    "user's notes: confirm with the user first. If a result says DRY RUN, nothing was "
    "written; tell the user. Note paths are relative to the vault."
)


@contextlib.asynccontextmanager
async def open_vault(settings: ObsidianSettings, dry_run: bool) -> AsyncGenerator[Vault]:
    """Open the vault client. Nothing is read until a tool is called.

    A missing vault is reported by each tool and by ``doctor``, not here, so the other
    products keep working while it is fixed.

    Args:
        settings: The ``[obsidian]`` settings.
        dry_run: Whether writes to the vault are blocked.

    Yields:
        The vault client.
    """
    yield Vault(settings, dry_run)


async def doctor(vault: Vault) -> AsyncIterator[str]:
    """Check that the vault exists, is an Obsidian vault, and count its tasks.

    Args:
        vault: The vault client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    status = await vault.status()
    if not status.is_vault:
        raise ConfigError(
            f"{status.vault} has no .obsidian folder, so it is not an Obsidian vault's top "
            "folder. Point 'vault' in the [obsidian] table at the folder that holds it."
        )
    yield f"vault {status.vault} holds {status.notes} note(s)"
    yield (
        f"{status.tasks} task note(s) tagged #{status.task_tag}, {status.open_tasks} not finished"
    )
    folder = vault.root / status.tasks_folder
    if folder.is_dir():
        yield f"tasks folder {status.tasks_folder or '(vault top)'} exists"
    else:
        yield f"tasks folder {status.tasks_folder} will be created with the first new task"
    layout = daily_layout(vault.settings)
    day = today(vault.settings)
    todays = layout.path(day)
    exists = "exists" if (vault.root / todays).is_file() else "not written yet"
    yield f"today is {day.isoformat()} in {describe_zone(vault.settings)}"
    yield f"daily notes go to {layout.pattern} ({layout.source}); today's, {todays}, {exists}"
    yield f"meeting notes go to {meeting_example(vault.settings, day)}"
    if status.unreadable:
        yield f"{len(status.unreadable)} note(s) have unreadable properties; see obsidian_status"
    if layout.differs_from_obsidian and layout.obsidian is not None:
        folder, date_format = layout.obsidian
        theirs = f"{folder}/{date_format}" if folder else date_format
        raise ConfigError(
            f"Daily notes in {FROM_CONFIG} ({layout.pattern}) differ from Obsidian's Daily "
            f"notes settings ({theirs}), so Obsidian's Today button would open a different "
            "note from the one written here. Make them match, or remove daily_folder and "
            "daily_format from [obsidian] to follow Obsidian."
        )


PRODUCT: LocalProduct[ObsidianSettings, Vault] = LocalProduct(
    name="obsidian",
    title="Obsidian",
    instructions=INSTRUCTIONS,
    settings_type=ObsidianSettings,
    parse_settings=parse_obsidian_settings,
    open=open_vault,
    register_tools=register_obsidian_tools,
    toolsets={
        "tasks": "Create and update task notes.",
        "planning": "Sync tasks and meetings into the vault and write sections of the daily note.",
    },
    doctor=doctor,
)
