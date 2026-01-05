"""State commands - unified state management and querying."""

import typer

from .status import status_command, urls_command, history_command
from .agents import agents_command, stale_command
from .validate import validate_command
from .prs import prs_command, refresh_prs_command
from .cleanup import cleanup_command, rebuild_command, export_command
from .sync_report import populate_addresses_command, sync_report_command
from .diff_remotes import diff_remotes_command

state_app = typer.Typer(help="Query and manage agent state database")

# Register commands
state_app.command("status")(status_command)
state_app.command("urls")(urls_command)
state_app.command("history")(history_command)
state_app.command("agents")(agents_command)
state_app.command("stale")(stale_command)
state_app.command("validate")(validate_command)
state_app.command("prs")(prs_command)
state_app.command("refresh-prs")(refresh_prs_command)
state_app.command("cleanup")(cleanup_command)
state_app.command("rebuild")(rebuild_command)
state_app.command("export")(export_command)
state_app.command("populate-addresses")(populate_addresses_command)
state_app.command("sync-report")(sync_report_command)
state_app.command("diff-remotes")(diff_remotes_command)
