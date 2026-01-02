"""Dolphin debugging interface for Melee."""

from .debugger import DolphinDebugger, ConnectionMode, Symbol, Breakpoint
from .rsp_client import GDBClient
from .launcher import DolphinLauncher
from .memory_client import DolphinMemory, MeleeAddresses, get_player_state

__all__ = [
    # Main interface
    "DolphinDebugger",
    "ConnectionMode",
    "Symbol",
    "Breakpoint",
    # Low-level
    "GDBClient",
    "DolphinLauncher",
    "DolphinMemory",
    "MeleeAddresses",
    "get_player_state",
]
