"""Sync commands - sync scratches to production decomp.me."""

import typer

from .auth import auth_command, status_command, clear_command
from .list_cmd import list_command, slugs_command
from .production import production_command
from .fix_ownership import fix_ownership_command
from .validate import validate_command, dedup_command, find_duplicates_command

sync_app = typer.Typer(help="Sync scratches to production decomp.me")

# Register commands
sync_app.command("status")(status_command)
sync_app.command("auth")(auth_command)
sync_app.command("list")(list_command)
sync_app.command("production")(production_command)
sync_app.command("slugs")(slugs_command)
sync_app.command("clear")(clear_command)
sync_app.command("fix-ownership")(fix_ownership_command)
sync_app.command("validate")(validate_command)
sync_app.command("dedup")(dedup_command)
sync_app.command("find-duplicates")(find_duplicates_command)
