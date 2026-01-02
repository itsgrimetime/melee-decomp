#!/usr/bin/env python3
"""
Dolphin debugging CLI for Melee.

Usage:
    melee-debug daemon start              # Start persistent daemon (required for breakpoints)
    melee-debug daemon stop               # Stop daemon
    melee-debug launch [--iso PATH]       # Launch Dolphin (use with daemon)
    melee-debug connect [--gdb | --memory]
    melee-debug read <address> [--count N] [--format FORMAT]
    melee-debug write <address> <value>
    melee-debug break <address> [--remove]
    melee-debug watch <address> [--size N] [--read] [--write]
    melee-debug step [--count N]
    melee-debug continue
    melee-debug halt
    melee-debug regs
    melee-debug symbol <name>
    melee-debug status
    melee-debug interactive
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .debugger import DolphinDebugger, ConnectionMode
from . import daemon as dbg_daemon


# Default paths
DEFAULT_ISO = Path.home() / "Downloads/ssbm_v1.02_original.iso"
DOLPHIN_DEBUG_APP = Path.home() / "Applications/Dolphin-Debug.app"
SYMBOLS_FILE = Path(__file__).parent.parent.parent / "melee/config/GALE01/symbols.txt"


class DaemonClient:
    """Client for communicating with the debug daemon."""

    def __init__(self):
        self.connected = False

    def is_available(self) -> bool:
        """Check if daemon is running."""
        return dbg_daemon.is_running()

    def send(self, cmd: dict, timeout: float = 60.0) -> dict:
        """Send command to daemon."""
        return dbg_daemon.send_command(cmd, timeout)


class MeleeDebugCLI:
    """CLI interface for Melee debugging."""

    def __init__(self):
        self.dbg = DolphinDebugger()
        self._symbols_loaded = False
        self._daemon = DaemonClient()

    def _use_daemon(self) -> bool:
        """Check if we should use the daemon."""
        return self._daemon.is_available()

    def _ensure_symbols(self):
        """Load symbols if not already loaded."""
        if not self._symbols_loaded and SYMBOLS_FILE.exists():
            count = self.dbg.load_symbols(SYMBOLS_FILE)
            self._symbols_loaded = True
            return count
        return 0

    def _format_address(self, addr: int) -> str:
        """Format an address, with symbol if known."""
        self._ensure_symbols()
        sym = self.dbg.get_symbol_at(addr)
        if sym:
            return f"0x{addr:08X} <{sym}>"
        return f"0x{addr:08X}"

    def cmd_launch(self, iso_path: str = None, wait: bool = True):
        """Launch Dolphin with GDB stub enabled."""
        iso = Path(iso_path) if iso_path else DEFAULT_ISO

        if not iso.exists():
            print(f"Error: ISO not found: {iso}")
            return 1

        dolphin = DOLPHIN_DEBUG_APP
        if not dolphin.exists():
            print(f"Error: Dolphin-Debug not found at {dolphin}")
            print("Run the resign_dolphin.sh script first.")
            return 1

        # Ensure GDB stub is configured
        config_file = Path.home() / "Library/Application Support/Dolphin/Config/Dolphin.ini"
        if config_file.exists():
            content = config_file.read_text()
            if "GDBPort" not in content or "[General]" not in content:
                # Add GDBPort to [General] section
                if "[General]" in content:
                    content = content.replace("[General]", "[General]\nGDBPort = 9090")
                else:
                    content += "\n[General]\nGDBPort = 9090\n"
                config_file.write_text(content)
                print("Configured GDB stub on port 9090")

        print(f"Launching {dolphin.name} with {iso.name}...")
        subprocess.Popen(["open", "-a", str(dolphin), str(iso)])

        if wait:
            print("Waiting for GDB stub...")
            for i in range(30):
                time.sleep(1)
                if self.dbg.connect(timeout=1.0):
                    print(f"Connected! Game ID: {self.dbg.get_game_id()}")
                    return 0
            print("Timeout waiting for GDB stub")
            return 1

        return 0

    def cmd_connect(self, mode: str = "auto"):
        """Connect to running Dolphin."""
        if mode == "gdb":
            conn_mode = ConnectionMode.GDB
        elif mode == "memory":
            conn_mode = ConnectionMode.MEMORY_ENGINE
        else:
            conn_mode = ConnectionMode.AUTO

        self.dbg.mode = conn_mode

        print(f"Connecting ({mode} mode)...")
        if self.dbg.connect(timeout=5.0):
            print(f"Connected!")
            if self.dbg.has_gdb:
                print("  GDB stub: active")
            if self.dbg.has_memory_engine:
                print("  Memory engine: active")
            game_id = self.dbg.get_game_id()
            if game_id:
                print(f"  Game: {game_id}")
            return 0
        else:
            print("Failed to connect to Dolphin")
            print("Make sure Dolphin-Debug is running with a game loaded")
            return 1

    def cmd_read(self, address: str, count: int = 16, fmt: str = "hex"):
        """Read memory at address."""
        if not self._ensure_connected():
            return 1

        addr = self.dbg.resolve_address(address)
        if addr is None:
            print(f"Error: Cannot resolve address '{address}'")
            return 1

        data = self.dbg.read_bytes(addr, count)
        if data is None:
            print(f"Error: Failed to read memory at {self._format_address(addr)}")
            return 1

        print(f"Memory at {self._format_address(addr)}:")

        if fmt == "hex":
            # Hex dump format
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                print(f"  {addr+i:08X}  {hex_part:<48}  {ascii_part}")

        elif fmt == "u32":
            for i in range(0, len(data), 4):
                if i + 4 <= len(data):
                    val = int.from_bytes(data[i:i+4], 'big')
                    print(f"  {addr+i:08X}: 0x{val:08X} ({val})")

        elif fmt == "f32":
            import struct
            for i in range(0, len(data), 4):
                if i + 4 <= len(data):
                    val = struct.unpack(">f", data[i:i+4])[0]
                    print(f"  {addr+i:08X}: {val:.6f}")

        elif fmt == "string":
            s = self.dbg.read_string(addr, count)
            print(f"  \"{s}\"")

        return 0

    def cmd_write(self, address: str, value: str):
        """Write value to memory."""
        if not self._ensure_connected():
            return 1

        addr = self.dbg.resolve_address(address)
        if addr is None:
            print(f"Error: Cannot resolve address '{address}'")
            return 1

        # Determine value type and write
        try:
            if value.startswith("0x"):
                int_val = int(value, 16)
            elif "." in value:
                float_val = float(value)
                if self.dbg.write_f32(addr, float_val):
                    print(f"Wrote {float_val} to {self._format_address(addr)}")
                    return 0
                else:
                    print("Write failed")
                    return 1
            else:
                int_val = int(value)

            if self.dbg.write_u32(addr, int_val):
                print(f"Wrote 0x{int_val:08X} to {self._format_address(addr)}")
                return 0
            else:
                print("Write failed")
                return 1
        except ValueError:
            print(f"Error: Invalid value '{value}'")
            return 1

    def cmd_break(self, address: str, remove: bool = False):
        """Set or remove a breakpoint."""
        # Use daemon if available (preferred for persistent connections)
        if self._use_daemon():
            result = self._daemon.send({
                "action": "break",
                "address": address,
                "remove": remove
            })
            if result.get("success"):
                data = result.get("data", {})
                if remove:
                    print(f"Removed breakpoint at {address}")
                else:
                    addr = data.get("address", 0)
                    sym = data.get("symbol", "")
                    if sym:
                        print(f"Set breakpoint at 0x{addr:08X} <{sym}>")
                    else:
                        print(f"Set breakpoint at 0x{addr:08X}")
                return 0
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                return 1

        # Direct connection (one-shot)
        if not self._ensure_connected():
            return 1

        if not self.dbg.has_gdb:
            print("Error: Breakpoints require GDB stub connection")
            print("Tip: Use 'melee-debug daemon start' for persistent debugging")
            return 1

        self._ensure_symbols()
        addr = self.dbg.resolve_address(address)
        if addr is None:
            print(f"Error: Cannot resolve address '{address}'")
            return 1

        if remove:
            if self.dbg.remove_breakpoint(addr):
                print(f"Removed breakpoint at {self._format_address(addr)}")
                return 0
            else:
                print("Failed to remove breakpoint")
                return 1
        else:
            if self.dbg.set_breakpoint(addr):
                print(f"Set breakpoint at {self._format_address(addr)}")
                return 0
            else:
                print("Failed to set breakpoint")
                return 1

    def cmd_watch(self, address: str, size: int = 4, read: bool = False, write: bool = True):
        """Set a memory watchpoint."""
        if not self._ensure_connected():
            return 1

        if not self.dbg.has_gdb:
            print("Error: Watchpoints require GDB stub connection")
            return 1

        addr = self.dbg.resolve_address(address)
        if addr is None:
            print(f"Error: Cannot resolve address '{address}'")
            return 1

        mode = []
        if read:
            mode.append("read")
        if write:
            mode.append("write")

        if self.dbg.set_watchpoint(addr, size, write=write, read=read):
            print(f"Set {'/'.join(mode)} watchpoint at {self._format_address(addr)} ({size} bytes)")
            return 0
        else:
            print("Failed to set watchpoint")
            return 1

    def cmd_step(self, count: int = 1):
        """Single-step instructions."""
        if self._use_daemon():
            result = self._daemon.send({"action": "step", "count": count})
            if result.get("success"):
                print(f"Stepped {count} instruction(s)")
                return 0
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                return 1

        if not self._ensure_connected():
            return 1

        if not self.dbg.has_gdb:
            print("Error: Stepping requires GDB stub connection")
            return 1

        for i in range(count):
            result = self.dbg.step()
            if result:
                print(f"Step {i+1}: {result}")
            else:
                print(f"Step {i+1}: (no response)")

        return 0

    def cmd_continue(self):
        """Continue execution."""
        if self._use_daemon():
            print("Continuing execution (will block until breakpoint hit)...")
            result = self._daemon.send({"action": "continue"}, timeout=300.0)
            if result.get("success"):
                stop_reason = result.get("data")
                print(f"Stopped: {stop_reason}")
                return 0
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                return 1

        if not self._ensure_connected():
            return 1

        if not self.dbg.has_gdb:
            print("Error: Continue requires GDB stub connection")
            return 1

        print("Continuing execution (Ctrl+C to interrupt)...")
        try:
            result = self.dbg.continue_execution()
            if result:
                print(f"Stopped: {result}")
        except KeyboardInterrupt:
            self.dbg.halt()
            print("\nHalted")

        return 0

    def cmd_halt(self):
        """Halt execution."""
        if self._use_daemon():
            result = self._daemon.send({"action": "halt"})
            if result.get("success"):
                print("Sent halt signal")
                return 0
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                return 1

        if not self._ensure_connected():
            return 1

        if self.dbg.halt():
            print("Sent halt signal")
            return 0
        else:
            print("Failed to halt")
            return 1

    def cmd_regs(self):
        """Display registers."""
        if self._use_daemon():
            result = self._daemon.send({"action": "regs"})
            if result.get("success"):
                regs = result.get("data", {})
                print("General Purpose Registers:")
                gprs = regs.get("gpr", [])
                for i in range(0, min(32, len(gprs)), 4):
                    line = "  "
                    for j in range(4):
                        if i + j < len(gprs):
                            line += f"r{i+j:2d}=0x{gprs[i+j]:08X}  "
                    print(line)
                return 0
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")
                return 1

        if not self._ensure_connected():
            return 1

        if not self.dbg.has_gdb:
            print("Error: Register access requires GDB stub connection")
            return 1

        regs = self.dbg.read_registers()
        if not regs:
            print("Failed to read registers")
            return 1

        print("General Purpose Registers:")
        gprs = regs.get("gpr", [])
        for i in range(0, min(32, len(gprs)), 4):
            line = "  "
            for j in range(4):
                if i + j < len(gprs):
                    line += f"r{i+j:2d}=0x{gprs[i+j]:08X}  "
            print(line)

        return 0

    def cmd_symbol(self, name: str):
        """Look up a symbol."""
        self._ensure_symbols()

        # Try exact match
        sym = self.dbg.get_symbol(name)
        if sym:
            print(f"{sym.name}:")
            print(f"  Address: 0x{sym.address:08X}")
            print(f"  Type: {sym.sym_type}")
            if sym.size:
                print(f"  Size: 0x{sym.size:X} ({sym.size} bytes)")
            return 0

        # Try partial match
        matches = [s for s in self.dbg.symbols.values() if name.lower() in s.name.lower()]
        if matches:
            print(f"Found {len(matches)} matching symbols:")
            for sym in matches[:20]:
                print(f"  0x{sym.address:08X}  {sym.name}")
            if len(matches) > 20:
                print(f"  ... and {len(matches) - 20} more")
            return 0

        print(f"Symbol '{name}' not found")
        return 1

    def cmd_status(self):
        """Show connection status."""
        print("Dolphin Debug Status:")

        # Check daemon first
        if self._use_daemon():
            result = self._daemon.send({"action": "status"})
            if result.get("success"):
                data = result.get("data", {})
                print("  Mode: daemon (persistent)")
                print(f"  Connected: {data.get('connected', False)}")
                print(f"  GDB stub: {'active' if data.get('has_gdb') else 'inactive'}")
                print(f"  Memory engine: {'active' if data.get('has_memory') else 'inactive'}")
                if data.get("game_id"):
                    print(f"  Game ID: {data['game_id']}")
                print(f"  Symbols loaded: {data.get('symbols', 0)}")
                print(f"  Breakpoints: {data.get('breakpoints', 0)}")
                return 0
            else:
                print(f"  Daemon error: {result.get('error')}")
                return 1

        print("  Mode: direct (one-shot)")
        print(f"  Daemon: not running (use 'daemon start' for persistent debugging)")
        print(f"  Connected: {self.dbg.is_connected}")
        print(f"  GDB stub: {'active' if self.dbg.has_gdb else 'inactive'}")
        print(f"  Memory engine: {'active' if self.dbg.has_memory_engine else 'inactive'}")

        if self.dbg.is_connected:
            game_id = self.dbg.get_game_id()
            if game_id:
                print(f"  Game ID: {game_id}")
            frame = self.dbg.get_frame_count()
            if frame is not None:
                print(f"  Frame: {frame}")

        print(f"  Symbols loaded: {len(self.dbg.symbols)}")
        print(f"  Breakpoints: {len(self.dbg.breakpoints)}")

        return 0

    def cmd_interactive(self):
        """Enter interactive debugging mode."""
        if not self._ensure_connected():
            return 1

        self._ensure_symbols()

        print("Melee Debug Interactive Mode")
        print("Commands: r[ead] w[rite] b[reak] s[tep] c[ont] h[alt] reg sym q[uit]")
        print()

        while True:
            try:
                line = input("melee> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            try:
                if cmd in ("q", "quit", "exit"):
                    break
                elif cmd in ("r", "read"):
                    if len(parts) >= 2:
                        count = int(parts[2], 0) if len(parts) > 2 else 16
                        self.cmd_read(parts[1], count)
                    else:
                        print("Usage: read <address> [count]")
                elif cmd in ("w", "write"):
                    if len(parts) >= 3:
                        self.cmd_write(parts[1], parts[2])
                    else:
                        print("Usage: write <address> <value>")
                elif cmd in ("b", "break"):
                    if len(parts) >= 2:
                        self.cmd_break(parts[1], remove="-r" in parts or "--remove" in parts)
                    else:
                        print("Usage: break <address> [-r]")
                elif cmd in ("s", "step"):
                    count = int(parts[1]) if len(parts) > 1 else 1
                    self.cmd_step(count)
                elif cmd in ("c", "cont", "continue"):
                    self.cmd_continue()
                elif cmd in ("h", "halt", "stop"):
                    self.cmd_halt()
                elif cmd in ("reg", "regs", "registers"):
                    self.cmd_regs()
                elif cmd in ("sym", "symbol"):
                    if len(parts) >= 2:
                        self.cmd_symbol(parts[1])
                    else:
                        print("Usage: sym <name>")
                elif cmd == "status":
                    self.cmd_status()
                elif cmd == "help":
                    print("Commands:")
                    print("  read <addr> [count]    Read memory")
                    print("  write <addr> <value>   Write memory")
                    print("  break <addr> [-r]      Set/remove breakpoint")
                    print("  step [count]           Single-step")
                    print("  cont                   Continue execution")
                    print("  halt                   Stop execution")
                    print("  regs                   Show registers")
                    print("  sym <name>             Look up symbol")
                    print("  status                 Show status")
                    print("  quit                   Exit")
                else:
                    print(f"Unknown command: {cmd}")
            except Exception as e:
                print(f"Error: {e}")

        return 0

    def _ensure_connected(self) -> bool:
        """Ensure we're connected to Dolphin."""
        if self.dbg.is_connected:
            self._ensure_symbols()
            return True

        print("Not connected. Attempting to connect...")
        if self.dbg.connect(timeout=2.0):
            self._ensure_symbols()
            return True

        print("Failed to connect. Use 'melee-debug connect' or 'melee-debug launch'")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Dolphin debugging CLI for Melee",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # daemon
    p_daemon = subparsers.add_parser("daemon", help="Manage persistent debug daemon")
    p_daemon.add_argument("action", choices=["start", "stop", "status"], help="Daemon action")

    # launch
    p_launch = subparsers.add_parser("launch", help="Launch Dolphin with GDB stub")
    p_launch.add_argument("--iso", help="Path to Melee ISO")
    p_launch.add_argument("--no-wait", action="store_true", help="Don't wait for connection")

    # connect
    p_connect = subparsers.add_parser("connect", help="Connect to running Dolphin")
    p_connect.add_argument("--gdb", action="store_true", help="Use GDB stub only")
    p_connect.add_argument("--memory", action="store_true", help="Use memory engine only")

    # read
    p_read = subparsers.add_parser("read", help="Read memory")
    p_read.add_argument("address", help="Address or symbol name")
    p_read.add_argument("-n", "--count", type=int, default=16, help="Bytes to read")
    p_read.add_argument("-f", "--format", choices=["hex", "u32", "f32", "string"], default="hex")

    # write
    p_write = subparsers.add_parser("write", help="Write memory")
    p_write.add_argument("address", help="Address or symbol name")
    p_write.add_argument("value", help="Value to write")

    # break
    p_break = subparsers.add_parser("break", help="Set/remove breakpoint")
    p_break.add_argument("address", help="Address or symbol name")
    p_break.add_argument("-r", "--remove", action="store_true", help="Remove breakpoint")

    # watch
    p_watch = subparsers.add_parser("watch", help="Set memory watchpoint")
    p_watch.add_argument("address", help="Address or symbol name")
    p_watch.add_argument("-s", "--size", type=int, default=4, help="Watch size")
    p_watch.add_argument("--read", action="store_true", help="Watch reads")
    p_watch.add_argument("--write", action="store_true", default=True, help="Watch writes")

    # step
    p_step = subparsers.add_parser("step", help="Single-step")
    p_step.add_argument("-n", "--count", type=int, default=1, help="Steps to take")

    # continue
    subparsers.add_parser("continue", help="Continue execution")
    subparsers.add_parser("cont", help="Continue execution (alias)")

    # halt
    subparsers.add_parser("halt", help="Halt execution")

    # regs
    subparsers.add_parser("regs", help="Show registers")

    # symbol
    p_sym = subparsers.add_parser("symbol", help="Look up symbol")
    p_sym.add_argument("name", help="Symbol name (partial match)")

    # status
    subparsers.add_parser("status", help="Show connection status")

    # interactive
    subparsers.add_parser("interactive", help="Interactive mode")
    subparsers.add_parser("i", help="Interactive mode (alias)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Handle daemon command separately (doesn't need CLI instance)
    if args.command == "daemon":
        if args.action == "start":
            if dbg_daemon.is_running():
                print("Daemon already running")
                return 1
            print("Starting daemon (this will block)...")
            print("Tip: Run in background with: python -m src.dolphin_debug.cli daemon start &")
            daemon = dbg_daemon.DebugDaemon()
            return daemon.start()
        elif args.action == "stop":
            return dbg_daemon.stop_daemon()
        elif args.action == "status":
            if dbg_daemon.is_running():
                print("Daemon is running")
                try:
                    result = dbg_daemon.send_command({"action": "status"}, timeout=2.0)
                    if result.get("success"):
                        data = result["data"]
                        print(f"  Connected: {data['connected']}")
                        print(f"  GDB: {data['has_gdb']}")
                        print(f"  Game: {data['game_id']}")
                        print(f"  Breakpoints: {data['breakpoints']}")
                        print(f"  Symbols: {data['symbols']}")
                except Exception as e:
                    print(f"  (could not query daemon: {e})")
                return 0
            else:
                print("Daemon not running")
                return 1

    cli = MeleeDebugCLI()

    if args.command == "launch":
        return cli.cmd_launch(args.iso, wait=not args.no_wait)
    elif args.command == "connect":
        mode = "gdb" if args.gdb else "memory" if args.memory else "auto"
        return cli.cmd_connect(mode)
    elif args.command == "read":
        return cli.cmd_read(args.address, args.count, args.format)
    elif args.command == "write":
        return cli.cmd_write(args.address, args.value)
    elif args.command == "break":
        return cli.cmd_break(args.address, args.remove)
    elif args.command == "watch":
        return cli.cmd_watch(args.address, args.size, args.read, args.write)
    elif args.command == "step":
        return cli.cmd_step(args.count)
    elif args.command in ("continue", "cont"):
        return cli.cmd_continue()
    elif args.command == "halt":
        return cli.cmd_halt()
    elif args.command == "regs":
        return cli.cmd_regs()
    elif args.command == "symbol":
        return cli.cmd_symbol(args.name)
    elif args.command == "status":
        return cli.cmd_status()
    elif args.command in ("interactive", "i"):
        return cli.cmd_interactive()

    return 1


if __name__ == "__main__":
    sys.exit(main())
