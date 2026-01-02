"""
Dolphin memory access client using dolphin-memory-engine.

This provides memory read/write access to a running Dolphin instance.
Works on ARM Mac when Dolphin is signed with debug entitlements.
"""

import struct
from typing import Optional, List
import dolphin_memory_engine as dme


class DolphinMemory:
    """High-level interface for Dolphin memory access."""

    def __init__(self):
        self._hooked = False

    def connect(self, max_attempts: int = 10, delay: float = 1.0) -> bool:
        """
        Connect to a running Dolphin instance.

        Args:
            max_attempts: Maximum connection attempts
            delay: Delay between attempts in seconds

        Returns:
            True if connected successfully
        """
        import time

        for attempt in range(max_attempts):
            dme.hook()
            if dme.is_hooked():
                self._hooked = True
                return True
            if attempt < max_attempts - 1:
                time.sleep(delay)

        return False

    def disconnect(self):
        """Disconnect from Dolphin (unhook)."""
        dme.un_hook()
        self._hooked = False

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to Dolphin."""
        return dme.is_hooked()

    # Memory read operations

    def read_bytes(self, address: int, length: int) -> bytes:
        """Read raw bytes from memory."""
        return bytes([dme.read_byte(address + i) for i in range(length)])

    def read_u8(self, address: int) -> int:
        """Read unsigned 8-bit value."""
        return dme.read_byte(address)

    def read_u16(self, address: int) -> int:
        """Read unsigned 16-bit big-endian value."""
        data = self.read_bytes(address, 2)
        return struct.unpack(">H", data)[0]

    def read_u32(self, address: int) -> int:
        """Read unsigned 32-bit big-endian value."""
        return dme.read_word(address)

    def read_s8(self, address: int) -> int:
        """Read signed 8-bit value."""
        val = dme.read_byte(address)
        return val if val < 128 else val - 256

    def read_s16(self, address: int) -> int:
        """Read signed 16-bit big-endian value."""
        data = self.read_bytes(address, 2)
        return struct.unpack(">h", data)[0]

    def read_s32(self, address: int) -> int:
        """Read signed 32-bit big-endian value."""
        data = self.read_bytes(address, 4)
        return struct.unpack(">i", data)[0]

    def read_f32(self, address: int) -> float:
        """Read 32-bit big-endian float."""
        return dme.read_float(address)

    def read_f64(self, address: int) -> float:
        """Read 64-bit big-endian double."""
        return dme.read_double(address)

    def read_string(self, address: int, max_length: int = 256) -> str:
        """Read null-terminated ASCII string."""
        chars = []
        for i in range(max_length):
            byte = dme.read_byte(address + i)
            if byte == 0:
                break
            chars.append(chr(byte))
        return "".join(chars)

    # Memory write operations

    def write_bytes(self, address: int, data: bytes):
        """Write raw bytes to memory."""
        for i, byte in enumerate(data):
            dme.write_byte(address + i, byte)

    def write_u8(self, address: int, value: int):
        """Write unsigned 8-bit value."""
        dme.write_byte(address, value & 0xFF)

    def write_u16(self, address: int, value: int):
        """Write unsigned 16-bit big-endian value."""
        self.write_bytes(address, struct.pack(">H", value))

    def write_u32(self, address: int, value: int):
        """Write unsigned 32-bit big-endian value."""
        dme.write_word(address, value)

    def write_f32(self, address: int, value: float):
        """Write 32-bit big-endian float."""
        dme.write_float(address, value)

    def write_f64(self, address: int, value: float):
        """Write 64-bit big-endian double."""
        dme.write_double(address, value)

    # Convenience methods

    def read_struct(self, address: int, format_string: str) -> tuple:
        """
        Read a struct from memory.

        Args:
            address: Memory address
            format_string: struct format (use > prefix for big-endian)

        Returns:
            Tuple of unpacked values
        """
        size = struct.calcsize(format_string)
        data = self.read_bytes(address, size)
        return struct.unpack(format_string, data)


# Known Melee memory addresses (NTSC 1.02 - GALE01)
class MeleeAddresses:
    """Known memory addresses for Melee NTSC 1.02."""

    # Game identification
    GAME_ID = 0x80000000  # "GALE01"
    VERSION = 0x80000007  # Version byte

    # Match state
    SCENE_CONTROLLER = 0x80479D30  # Scene/menu controller
    FRAME_COUNTER = 0x80479D60  # Global frame counter

    # Stage
    STAGE_ID = 0x8049E6C8  # Current stage ID
    STAGE_INFO = 0x8049E6C0  # Stage info struct

    # Player slots (base addresses)
    # Each player is 0xE90 bytes apart
    PLAYER_BLOCK_BASE = 0x80453080
    PLAYER_BLOCK_SIZE = 0xE90

    @classmethod
    def player_block(cls, port: int) -> int:
        """Get base address for player data (port 0-3)."""
        return cls.PLAYER_BLOCK_BASE + (port * cls.PLAYER_BLOCK_SIZE)

    # Player data offsets (from player block base)
    class PlayerOffsets:
        ACTION_STATE = 0x10  # Current action state
        FACING_DIRECTION = 0x2C  # 1.0 = right, -1.0 = left
        POS_X = 0xB0  # X position (float)
        POS_Y = 0xB4  # Y position (float)
        POS_Z = 0xB8  # Z position (float)
        VEL_X = 0x80  # X velocity (float)
        VEL_Y = 0x84  # Y velocity (float)
        PERCENT = 0x1830  # Damage percent
        STOCKS = 0x1F3C  # Stock count
        CHARACTER = 0x4  # Internal character ID
        COSTUME = 0x6  # Costume index


def get_player_state(mem: DolphinMemory, port: int) -> dict:
    """
    Read current state for a player.

    Args:
        mem: Connected DolphinMemory instance
        port: Player port (0-3)

    Returns:
        Dictionary with player state
    """
    base = MeleeAddresses.player_block(port)
    offs = MeleeAddresses.PlayerOffsets

    return {
        "port": port,
        "character": mem.read_u8(base + offs.CHARACTER),
        "costume": mem.read_u8(base + offs.COSTUME),
        "action_state": mem.read_u32(base + offs.ACTION_STATE),
        "facing": mem.read_f32(base + offs.FACING_DIRECTION),
        "position": {
            "x": mem.read_f32(base + offs.POS_X),
            "y": mem.read_f32(base + offs.POS_Y),
            "z": mem.read_f32(base + offs.POS_Z),
        },
        "velocity": {
            "x": mem.read_f32(base + offs.VEL_X),
            "y": mem.read_f32(base + offs.VEL_Y),
        },
        "percent": mem.read_f32(base + offs.PERCENT),
        "stocks": mem.read_u8(base + offs.STOCKS),
    }
