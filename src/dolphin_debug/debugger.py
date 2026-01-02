"""
Unified Dolphin debugger interface.

Provides a consistent API for debugging Melee via either:
- GDB stub (breakpoints, stepping, registers)
- dolphin-memory-engine (fast memory access)
"""

import socket
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Callable

try:
    import dolphin_memory_engine as dme
    HAS_DME = True
except ImportError:
    HAS_DME = False


class ConnectionMode(Enum):
    """Connection mode for the debugger."""
    GDB = "gdb"
    MEMORY_ENGINE = "memory"
    AUTO = "auto"


@dataclass
class Breakpoint:
    """Represents a breakpoint."""
    address: int
    enabled: bool = True
    hit_count: int = 0
    condition: Optional[str] = None
    symbol: Optional[str] = None


@dataclass
class Symbol:
    """Represents a symbol from the decomp."""
    name: str
    address: int
    size: int = 0
    sym_type: str = "function"  # function, object, label


class DolphinDebugger:
    """
    Unified interface for Dolphin debugging.

    Supports both GDB stub (for breakpoints/stepping) and
    dolphin-memory-engine (for fast memory access).
    """

    # Dolphin config paths
    DOLPHIN_CONFIG_DIR = Path.home() / "Library/Application Support/Dolphin"
    DOLPHIN_DEBUG_APP = Path.home() / "Applications/Dolphin-Debug.app"
    DOLPHIN_APP = Path("/Applications/Dolphin.app")

    def __init__(
        self,
        mode: ConnectionMode = ConnectionMode.AUTO,
        gdb_host: str = "localhost",
        gdb_port: int = 9090,
    ):
        self.mode = mode
        self.gdb_host = gdb_host
        self.gdb_port = gdb_port

        # Connection state
        self._gdb_sock: Optional[socket.socket] = None
        self._dme_hooked = False
        self._connected = False

        # Debugging state
        self.breakpoints: dict[int, Breakpoint] = {}
        self.symbols: dict[str, Symbol] = {}
        self.symbols_by_addr: dict[int, Symbol] = {}

        # Callbacks
        self.on_breakpoint_hit: Optional[Callable[[int], None]] = None

    @property
    def is_connected(self) -> bool:
        """Check if connected to Dolphin."""
        if self._gdb_sock:
            return True
        if HAS_DME and self._dme_hooked:
            return dme.is_hooked()
        return False

    @property
    def has_gdb(self) -> bool:
        """Check if GDB stub is connected."""
        return self._gdb_sock is not None

    @property
    def has_memory_engine(self) -> bool:
        """Check if dolphin-memory-engine is connected."""
        return HAS_DME and dme.is_hooked()

    # === Connection Methods ===

    def connect(self, timeout: float = 10.0) -> bool:
        """
        Connect to Dolphin.

        In AUTO mode, tries GDB first, then memory-engine.
        """
        if self.mode == ConnectionMode.GDB:
            return self._connect_gdb(timeout)
        elif self.mode == ConnectionMode.MEMORY_ENGINE:
            return self._connect_dme()
        else:  # AUTO
            # Try GDB first
            if self._connect_gdb(timeout=2.0):
                return True
            # Fall back to memory engine
            return self._connect_dme()

    def _connect_gdb(self, timeout: float = 10.0) -> bool:
        """Connect to GDB stub."""
        try:
            self._gdb_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._gdb_sock.settimeout(timeout)
            self._gdb_sock.connect((self.gdb_host, self.gdb_port))
            self._connected = True
            return True
        except (socket.error, socket.timeout):
            self._gdb_sock = None
            return False

    def _connect_dme(self) -> bool:
        """Connect via dolphin-memory-engine."""
        if not HAS_DME:
            return False

        for _ in range(5):
            dme.hook()
            if dme.is_hooked():
                self._dme_hooked = True
                self._connected = True
                return True
            time.sleep(0.5)
        return False

    def disconnect(self):
        """Disconnect from Dolphin."""
        if self._gdb_sock:
            try:
                self._gdb_sock.close()
            except:
                pass
            self._gdb_sock = None

        if HAS_DME and self._dme_hooked:
            try:
                dme.un_hook()
            except:
                pass
            self._dme_hooked = False

        self._connected = False

    # === GDB Protocol ===

    def _gdb_checksum(self, data: bytes) -> int:
        return sum(data) % 256

    def _gdb_send(self, cmd: str) -> Optional[str]:
        """Send GDB command and get response."""
        if not self._gdb_sock:
            return None

        payload = cmd.encode('ascii')
        checksum = self._gdb_checksum(payload)
        packet = b"$" + payload + b"#" + f"{checksum:02x}".encode()

        try:
            self._gdb_sock.sendall(packet)
            return self._gdb_recv()
        except socket.error:
            return None

    def _gdb_recv(self, timeout: float = 2.0) -> Optional[str]:
        """Receive GDB response."""
        if not self._gdb_sock:
            return None

        self._gdb_sock.settimeout(timeout)
        try:
            data = b""
            while True:
                byte = self._gdb_sock.recv(1)
                if not byte:
                    break
                data += byte
                if b"#" in data and len(data) > data.find(b"#") + 2:
                    break

            # Send ACK
            if data and b"$" in data:
                self._gdb_sock.sendall(b"+")

            # Extract payload
            if b"$" in data and b"#" in data:
                start = data.find(b"$") + 1
                end = data.find(b"#")
                return data[start:end].decode('ascii', errors='replace')
        except socket.timeout:
            pass
        return None

    # === Memory Operations ===

    def read_bytes(self, address: int, length: int) -> Optional[bytes]:
        """Read raw bytes from memory."""
        # Prefer memory engine for speed
        if self.has_memory_engine:
            try:
                return bytes([dme.read_byte(address + i) for i in range(length)])
            except:
                pass

        # Fall back to GDB
        if self.has_gdb:
            resp = self._gdb_send(f"m{address:x},{length:x}")
            if resp and not resp.startswith('E'):
                try:
                    return bytes.fromhex(resp)
                except ValueError:
                    pass
        return None

    def read_u8(self, address: int) -> Optional[int]:
        data = self.read_bytes(address, 1)
        return data[0] if data else None

    def read_u16(self, address: int) -> Optional[int]:
        data = self.read_bytes(address, 2)
        return struct.unpack(">H", data)[0] if data else None

    def read_u32(self, address: int) -> Optional[int]:
        # Prefer memory engine
        if self.has_memory_engine:
            try:
                return dme.read_word(address)
            except:
                pass
        data = self.read_bytes(address, 4)
        return struct.unpack(">I", data)[0] if data else None

    def read_s32(self, address: int) -> Optional[int]:
        data = self.read_bytes(address, 4)
        return struct.unpack(">i", data)[0] if data else None

    def read_f32(self, address: int) -> Optional[float]:
        if self.has_memory_engine:
            try:
                return dme.read_float(address)
            except:
                pass
        data = self.read_bytes(address, 4)
        return struct.unpack(">f", data)[0] if data else None

    def read_string(self, address: int, max_len: int = 256) -> Optional[str]:
        """Read null-terminated string."""
        chars = []
        for i in range(max_len):
            b = self.read_u8(address + i)
            if b is None or b == 0:
                break
            chars.append(chr(b))
        return "".join(chars) if chars else None

    def write_bytes(self, address: int, data: bytes) -> bool:
        """Write raw bytes to memory."""
        if self.has_memory_engine:
            try:
                for i, b in enumerate(data):
                    dme.write_byte(address + i, b)
                return True
            except:
                pass

        if self.has_gdb:
            hex_data = data.hex()
            resp = self._gdb_send(f"M{address:x},{len(data):x}:{hex_data}")
            return resp == "OK"
        return False

    def write_u32(self, address: int, value: int) -> bool:
        if self.has_memory_engine:
            try:
                dme.write_word(address, value)
                return True
            except:
                pass
        return self.write_bytes(address, struct.pack(">I", value))

    def write_f32(self, address: int, value: float) -> bool:
        if self.has_memory_engine:
            try:
                dme.write_float(address, value)
                return True
            except:
                pass
        return self.write_bytes(address, struct.pack(">f", value))

    # === Breakpoint Operations (GDB only) ===

    def set_breakpoint(self, address: int, symbol: Optional[str] = None) -> bool:
        """Set a code breakpoint."""
        if not self.has_gdb:
            return False

        resp = self._gdb_send(f"Z0,{address:x},4")
        if resp == "OK":
            self.breakpoints[address] = Breakpoint(
                address=address,
                symbol=symbol or self.get_symbol_at(address)
            )
            return True
        return False

    def remove_breakpoint(self, address: int) -> bool:
        """Remove a breakpoint."""
        if not self.has_gdb:
            return False

        resp = self._gdb_send(f"z0,{address:x},4")
        if resp == "OK":
            self.breakpoints.pop(address, None)
            return True
        return False

    def set_watchpoint(self, address: int, size: int = 4, write: bool = True, read: bool = False) -> bool:
        """Set a memory watchpoint."""
        if not self.has_gdb:
            return False

        if write and read:
            wp_type = "4"
        elif write:
            wp_type = "2"
        elif read:
            wp_type = "3"
        else:
            return False

        resp = self._gdb_send(f"Z{wp_type},{address:x},{size:x}")
        return resp == "OK"

    # === Execution Control (GDB only) ===

    def continue_execution(self) -> Optional[str]:
        """Continue execution. Returns stop reason when target stops."""
        if not self.has_gdb:
            return None

        self._gdb_sock.sendall(b"$c#63")
        # This blocks until target stops
        return self._gdb_recv(timeout=60.0)

    def step(self) -> Optional[str]:
        """Single-step one instruction."""
        if not self.has_gdb:
            return None
        return self._gdb_send("s")

    def halt(self) -> bool:
        """Halt execution (send interrupt)."""
        if not self._gdb_sock:
            return False
        try:
            self._gdb_sock.sendall(b"\x03")
            return True
        except:
            return False

    # === Register Operations (GDB only) ===

    def read_registers(self) -> Optional[dict]:
        """Read all CPU registers."""
        if not self.has_gdb:
            return None

        resp = self._gdb_send("g")
        if not resp:
            return None

        try:
            data = bytes.fromhex(resp)
        except ValueError:
            return None

        regs = {"gpr": []}
        # 32 GPRs, 4 bytes each
        for i in range(32):
            if len(data) >= (i + 1) * 4:
                val = struct.unpack(">I", data[i*4:(i+1)*4])[0]
                regs["gpr"].append(val)

        return regs

    def read_pc(self) -> Optional[int]:
        """Read program counter."""
        regs = self.read_registers()
        # PC location varies by stub implementation
        # For Dolphin, it might be after GPRs
        return None  # TODO: determine PC offset

    # === Symbol Operations ===

    def load_symbols(self, symbols_path: Path) -> int:
        """Load symbols from decomp symbols.txt file."""
        import re

        count = 0
        with open(symbols_path) as f:
            for line in f:
                # Parse: name = .section:0xADDRESS; // type:function size:0xSIZE
                match = re.match(r'(\w+)\s*=\s*\.[^:]+:(0x[0-9A-Fa-f]+)', line)
                if match:
                    name = match.group(1)
                    addr = int(match.group(2), 16)

                    # Determine type and size
                    sym_type = "function" if "type:function" in line else "object"
                    size_match = re.search(r'size:(0x[0-9A-Fa-f]+)', line)
                    size = int(size_match.group(1), 16) if size_match else 0

                    sym = Symbol(name=name, address=addr, size=size, sym_type=sym_type)
                    self.symbols[name] = sym
                    self.symbols_by_addr[addr] = sym
                    count += 1

        return count

    def get_symbol(self, name: str) -> Optional[Symbol]:
        """Get symbol by name."""
        return self.symbols.get(name)

    def get_symbol_at(self, address: int) -> Optional[str]:
        """Get symbol name at address."""
        sym = self.symbols_by_addr.get(address)
        return sym.name if sym else None

    def resolve_address(self, name_or_addr: str) -> Optional[int]:
        """Resolve a symbol name or hex address to an address."""
        # Try as hex first
        if name_or_addr.startswith("0x"):
            try:
                return int(name_or_addr, 16)
            except ValueError:
                pass

        # Try as symbol
        sym = self.get_symbol(name_or_addr)
        if sym:
            return sym.address

        # Try as bare hex
        try:
            return int(name_or_addr, 16)
        except ValueError:
            pass

        return None

    # === Melee-Specific Helpers ===

    def get_game_id(self) -> Optional[str]:
        """Get the game ID (should be GALE01 for Melee NTSC 1.02)."""
        data = self.read_bytes(0x80000000, 6)
        return data.decode('ascii') if data else None

    def get_frame_count(self) -> Optional[int]:
        """Get the current frame counter."""
        return self.read_u32(0x80479D60)

    def is_melee(self) -> bool:
        """Check if the running game is Melee."""
        game_id = self.get_game_id()
        return game_id is not None and game_id.startswith("GALE")
