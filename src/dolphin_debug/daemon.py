"""
Persistent debugging daemon that holds the GDB connection.

Usage:
    python -m src.dolphin_debug.daemon start   # Start daemon (blocks)
    python -m src.dolphin_debug.daemon stop    # Stop daemon
    python -m src.dolphin_debug.daemon status  # Check if running
"""

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.dolphin_debug import DolphinDebugger, ConnectionMode

# Daemon config
SOCKET_PATH = Path("/tmp/dolphin_debug.sock")
PID_FILE = Path("/tmp/dolphin_debug.pid")
SYMBOLS_PATH = Path("/Users/mike/code/melee-decomp/melee/config/GALE01/symbols.txt")


class DebugDaemon:
    """Daemon that maintains GDB connection and serves commands."""

    def __init__(self):
        self.dbg: Optional[DolphinDebugger] = None
        self.running = False
        self.server_socket: Optional[socket.socket] = None

    def connect_to_dolphin(self, timeout: float = 30.0) -> bool:
        """Connect to Dolphin GDB stub."""
        print("Connecting to Dolphin GDB stub...")
        self.dbg = DolphinDebugger(mode=ConnectionMode.GDB)

        start = time.time()
        while time.time() - start < timeout:
            if self.dbg.connect(timeout=2.0):
                print(f"Connected! Game ID: {self.dbg.get_game_id()}")

                # Load symbols
                if SYMBOLS_PATH.exists():
                    count = self.dbg.load_symbols(SYMBOLS_PATH)
                    print(f"Loaded {count} symbols")

                return True
            time.sleep(1)

        print("Failed to connect to Dolphin")
        return False

    def handle_command(self, cmd: dict) -> dict:
        """Execute a command and return result."""
        action = cmd.get("action")
        result = {"success": False, "error": None, "data": None}

        if not self.dbg or not self.dbg.is_connected:
            result["error"] = "Not connected to Dolphin"
            return result

        try:
            if action == "status":
                result["data"] = {
                    "connected": self.dbg.is_connected,
                    "has_gdb": self.dbg.has_gdb,
                    "has_memory": self.dbg.has_memory_engine,
                    "game_id": self.dbg.get_game_id(),
                    "breakpoints": len(self.dbg.breakpoints),
                    "symbols": len(self.dbg.symbols),
                }
                result["success"] = True

            elif action == "read":
                addr = cmd.get("address")
                count = cmd.get("count", 4)
                fmt = cmd.get("format", "hex")

                # Resolve address
                if isinstance(addr, str):
                    resolved = self.dbg.resolve_address(addr)
                    if resolved is None:
                        result["error"] = f"Cannot resolve address: {addr}"
                        return result
                    addr = resolved

                data = self.dbg.read_bytes(addr, count)
                if data:
                    if fmt == "hex":
                        result["data"] = data.hex()
                    elif fmt == "u32":
                        import struct
                        result["data"] = struct.unpack(">I", data[:4])[0]
                    elif fmt == "f32":
                        import struct
                        result["data"] = struct.unpack(">f", data[:4])[0]
                    else:
                        result["data"] = list(data)
                    result["success"] = True
                else:
                    result["error"] = "Read failed"

            elif action == "write":
                addr = cmd.get("address")
                value = cmd.get("value")
                fmt = cmd.get("format", "u32")

                if isinstance(addr, str):
                    resolved = self.dbg.resolve_address(addr)
                    if resolved is None:
                        result["error"] = f"Cannot resolve address: {addr}"
                        return result
                    addr = resolved

                if fmt == "f32":
                    result["success"] = self.dbg.write_f32(addr, float(value))
                else:
                    result["success"] = self.dbg.write_u32(addr, int(value))

            elif action == "break":
                addr = cmd.get("address")
                remove = cmd.get("remove", False)

                if isinstance(addr, str):
                    symbol = addr
                    resolved = self.dbg.resolve_address(addr)
                    if resolved is None:
                        result["error"] = f"Cannot resolve address: {addr}"
                        return result
                    addr = resolved
                else:
                    symbol = self.dbg.get_symbol_at(addr)

                if remove:
                    result["success"] = self.dbg.remove_breakpoint(addr)
                else:
                    result["success"] = self.dbg.set_breakpoint(addr, symbol)
                    if result["success"]:
                        result["data"] = {"address": addr, "symbol": symbol}

            elif action == "watch":
                addr = cmd.get("address")
                size = cmd.get("size", 4)
                write = cmd.get("write", True)
                read = cmd.get("read", False)

                if isinstance(addr, str):
                    resolved = self.dbg.resolve_address(addr)
                    if resolved is None:
                        result["error"] = f"Cannot resolve address: {addr}"
                        return result
                    addr = resolved

                result["success"] = self.dbg.set_watchpoint(addr, size, write, read)

            elif action == "continue":
                result["data"] = self.dbg.continue_execution()
                result["success"] = True

            elif action == "step":
                count = cmd.get("count", 1)
                for _ in range(count):
                    self.dbg.step()
                result["success"] = True

            elif action == "halt":
                result["success"] = self.dbg.halt()

            elif action == "regs":
                regs = self.dbg.read_registers()
                if regs:
                    result["data"] = regs
                    result["success"] = True
                else:
                    result["error"] = "Failed to read registers"

            elif action == "symbol":
                pattern = cmd.get("pattern", "")
                matches = []
                for name, sym in self.dbg.symbols.items():
                    if pattern.lower() in name.lower():
                        matches.append({"name": name, "address": sym.address})
                        if len(matches) >= 50:
                            break
                result["data"] = matches
                result["success"] = True

            elif action == "resolve":
                addr = cmd.get("address")
                resolved = self.dbg.resolve_address(addr)
                if resolved is not None:
                    result["data"] = resolved
                    result["success"] = True
                else:
                    result["error"] = f"Cannot resolve: {addr}"

            else:
                result["error"] = f"Unknown action: {action}"

        except Exception as e:
            result["error"] = str(e)

        return result

    def handle_client(self, conn: socket.socket):
        """Handle a client connection."""
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            if data:
                cmd = json.loads(data.decode())
                result = self.handle_command(cmd)
                conn.sendall(json.dumps(result).encode() + b"\n")
        except Exception as e:
            error_result = {"success": False, "error": str(e)}
            try:
                conn.sendall(json.dumps(error_result).encode() + b"\n")
            except:
                pass
        finally:
            conn.close()

    def start(self):
        """Start the daemon."""
        # Clean up old socket
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()

        # Write PID file
        PID_FILE.write_text(str(os.getpid()))

        # Connect to Dolphin
        if not self.connect_to_dolphin():
            print("Could not connect to Dolphin. Is it running with a game loaded?")
            return 1

        # Set up signal handlers
        def shutdown(signum, frame):
            print("\nShutting down...")
            self.running = False
            if self.server_socket:
                self.server_socket.close()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        # Start server
        self.server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_socket.bind(str(SOCKET_PATH))
        self.server_socket.listen(5)
        self.server_socket.settimeout(1.0)

        self.running = True
        print(f"Daemon listening on {SOCKET_PATH}")
        print("Press Ctrl+C to stop")

        while self.running:
            try:
                conn, _ = self.server_socket.accept()
                # Handle in thread to not block
                threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

        # Cleanup
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        if PID_FILE.exists():
            PID_FILE.unlink()
        if self.dbg:
            self.dbg.disconnect()

        print("Daemon stopped")
        return 0


def send_command(cmd: dict, timeout: float = 60.0) -> dict:
    """Send command to daemon and get response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET_PATH))
        sock.sendall(json.dumps(cmd).encode() + b"\n")

        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break

        return json.loads(data.decode())
    finally:
        sock.close()


def is_running() -> bool:
    """Check if daemon is running."""
    if not PID_FILE.exists():
        return False

    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_daemon():
    """Stop the daemon."""
    if not PID_FILE.exists():
        print("Daemon not running")
        return 1

    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to daemon (PID {pid})")
        return 0
    except ProcessLookupError:
        print("Daemon not running")
        PID_FILE.unlink()
        return 1


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m src.dolphin_debug.daemon {start|stop|status}")
        return 1

    cmd = sys.argv[1]

    if cmd == "start":
        if is_running():
            print("Daemon already running")
            return 1
        daemon = DebugDaemon()
        return daemon.start()

    elif cmd == "stop":
        return stop_daemon()

    elif cmd == "status":
        if is_running():
            print("Daemon is running")
            # Try to get status from daemon
            try:
                result = send_command({"action": "status"}, timeout=2.0)
                if result.get("success"):
                    data = result["data"]
                    print(f"  Connected: {data['connected']}")
                    print(f"  GDB: {data['has_gdb']}")
                    print(f"  Memory: {data['has_memory']}")
                    print(f"  Game: {data['game_id']}")
                    print(f"  Breakpoints: {data['breakpoints']}")
                    print(f"  Symbols: {data['symbols']}")
            except Exception as e:
                print(f"  (could not query daemon: {e})")
            return 0
        else:
            print("Daemon not running")
            return 1

    else:
        print(f"Unknown command: {cmd}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
