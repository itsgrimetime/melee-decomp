"""
GDB Remote Serial Protocol client for Dolphin emulator.

Implements the GDB RSP protocol to communicate with Dolphin's GDB stub.
Reference: https://sourceware.org/gdb/current/onlinedocs/gdb.html/Remote-Protocol.html
"""

import socket
import struct
from typing import Optional


class GDBClient:
    """Client for GDB Remote Serial Protocol communication with Dolphin."""

    def __init__(self, host: str = "localhost", port: int = 9090):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self._no_ack_mode = False

    def connect(self, timeout: float = 10.0) -> bool:
        """Connect to the GDB stub server."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((self.host, self.port))
            # Wait for initial '+' acknowledgment
            ack = self.sock.recv(1)
            if ack == b"+":
                pass  # Some stubs send initial ack
            return True
        except (socket.error, socket.timeout) as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from the GDB stub server."""
        if self.sock:
            try:
                self.sock.close()
            except socket.error:
                pass
            self.sock = None

    def _checksum(self, data: bytes) -> int:
        """Calculate RSP checksum (sum of bytes mod 256)."""
        return sum(data) % 256

    def _send_packet(self, data: str) -> bool:
        """Send a packet with framing and checksum."""
        if not self.sock:
            return False

        payload = data.encode("ascii")
        checksum = self._checksum(payload)
        packet = b"$" + payload + b"#" + f"{checksum:02x}".encode("ascii")

        try:
            self.sock.sendall(packet)
            if not self._no_ack_mode:
                # Wait for acknowledgment
                ack = self.sock.recv(1)
                if ack != b"+":
                    print(f"Bad ack: {ack}")
                    return False
            return True
        except socket.error as e:
            print(f"Send failed: {e}")
            return False

    def _recv_packet(self, timeout: float = 5.0) -> Optional[str]:
        """Receive a packet, stripping framing and verifying checksum."""
        if not self.sock:
            return None

        self.sock.settimeout(timeout)
        try:
            # Read until we get '$'
            while True:
                byte = self.sock.recv(1)
                if not byte:
                    return None
                if byte == b"$":
                    break
                # Might get '+' acks mixed in

            # Read until '#'
            data = b""
            while True:
                byte = self.sock.recv(1)
                if not byte:
                    return None
                if byte == b"#":
                    break
                data += byte

            # Read 2-byte checksum
            checksum_hex = self.sock.recv(2)
            expected_checksum = int(checksum_hex, 16)
            actual_checksum = self._checksum(data)

            if actual_checksum != expected_checksum:
                print(f"Checksum mismatch: expected {expected_checksum:02x}, got {actual_checksum:02x}")
                if not self._no_ack_mode:
                    self.sock.sendall(b"-")  # NAK
                return None

            if not self._no_ack_mode:
                self.sock.sendall(b"+")  # ACK

            return data.decode("ascii", errors="replace")

        except socket.timeout:
            print("Receive timeout")
            return None
        except socket.error as e:
            print(f"Receive failed: {e}")
            return None

    def _command(self, cmd: str, timeout: float = 5.0) -> Optional[str]:
        """Send a command and return the response."""
        if not self._send_packet(cmd):
            return None
        return self._recv_packet(timeout)

    # High-level commands

    def read_memory(self, address: int, length: int) -> Optional[bytes]:
        """
        Read memory from the target.

        Args:
            address: Memory address (GameCube addresses start at 0x80000000)
            length: Number of bytes to read

        Returns:
            Bytes read, or None on failure
        """
        # GDB protocol: m<addr>,<length>
        # Address is sent without the 0x prefix
        response = self._command(f"m{address:x},{length:x}")
        if response is None or response.startswith("E"):
            return None

        # Response is hex-encoded bytes
        try:
            return bytes.fromhex(response)
        except ValueError:
            print(f"Invalid hex response: {response}")
            return None

    def read_u32(self, address: int) -> Optional[int]:
        """Read a 32-bit big-endian value (PowerPC native)."""
        data = self.read_memory(address, 4)
        if data is None:
            return None
        return struct.unpack(">I", data)[0]

    def read_u16(self, address: int) -> Optional[int]:
        """Read a 16-bit big-endian value."""
        data = self.read_memory(address, 2)
        if data is None:
            return None
        return struct.unpack(">H", data)[0]

    def read_u8(self, address: int) -> Optional[int]:
        """Read an 8-bit value."""
        data = self.read_memory(address, 1)
        if data is None:
            return None
        return data[0]

    def read_f32(self, address: int) -> Optional[float]:
        """Read a 32-bit big-endian float."""
        data = self.read_memory(address, 4)
        if data is None:
            return None
        return struct.unpack(">f", data)[0]

    def write_memory(self, address: int, data: bytes) -> bool:
        """
        Write memory to the target.

        Args:
            address: Memory address
            data: Bytes to write

        Returns:
            True on success
        """
        # GDB protocol: M<addr>,<length>:<hex data>
        hex_data = data.hex()
        response = self._command(f"M{address:x},{len(data):x}:{hex_data}")
        return response == "OK"

    def write_u32(self, address: int, value: int) -> bool:
        """Write a 32-bit big-endian value."""
        return self.write_memory(address, struct.pack(">I", value))

    def read_registers(self) -> Optional[dict]:
        """
        Read all CPU registers.

        Returns dict with keys: gpr (list of 32), fpr (list of 32),
        pc, msr, cr, lr, ctr, xer
        """
        response = self._command("g")
        if response is None or response.startswith("E"):
            return None

        try:
            data = bytes.fromhex(response)
        except ValueError:
            return None

        # PowerPC register layout from GDB (varies by stub implementation)
        # Typically: 32 GPRs (4 bytes each), then FPRs, then special registers
        # This may need adjustment based on Dolphin's actual layout
        if len(data) < 128:  # At minimum 32 GPRs
            return None

        regs = {}
        offset = 0

        # 32 General Purpose Registers (32-bit each)
        regs["gpr"] = []
        for i in range(32):
            val = struct.unpack(">I", data[offset : offset + 4])[0]
            regs["gpr"].append(val)
            offset += 4

        # The rest depends on the stub's register layout
        # For now, just store the remaining data
        regs["_raw_remaining"] = data[offset:]

        return regs

    def set_breakpoint(self, address: int, kind: int = 4) -> bool:
        """
        Set a software breakpoint.

        Args:
            address: Address to break at
            kind: Breakpoint kind (4 = 4-byte instruction for PowerPC)
        """
        response = self._command(f"Z0,{address:x},{kind}")
        return response == "OK"

    def remove_breakpoint(self, address: int, kind: int = 4) -> bool:
        """Remove a software breakpoint."""
        response = self._command(f"z0,{address:x},{kind}")
        return response == "OK"

    def set_watchpoint(
        self, address: int, length: int, write: bool = True, read: bool = False
    ) -> bool:
        """
        Set a memory watchpoint.

        Args:
            address: Address to watch
            length: Size of region
            write: Break on write
            read: Break on read
        """
        if write and read:
            wp_type = "4"  # Access watchpoint
        elif write:
            wp_type = "2"  # Write watchpoint
        elif read:
            wp_type = "3"  # Read watchpoint
        else:
            return False

        response = self._command(f"Z{wp_type},{address:x},{length}")
        return response == "OK"

    def remove_watchpoint(
        self, address: int, length: int, write: bool = True, read: bool = False
    ) -> bool:
        """Remove a memory watchpoint."""
        if write and read:
            wp_type = "4"
        elif write:
            wp_type = "2"
        elif read:
            wp_type = "3"
        else:
            return False

        response = self._command(f"z{wp_type},{address:x},{length}")
        return response == "OK"

    def continue_execution(self) -> Optional[str]:
        """
        Continue execution until breakpoint or signal.

        Returns the stop reason (e.g., "S05" for SIGTRAP).
        """
        return self._command("c", timeout=60.0)

    def step(self) -> Optional[str]:
        """
        Single-step one instruction.

        Returns the stop reason.
        """
        return self._command("s")

    def halt(self) -> bool:
        """
        Halt the target (send interrupt).

        Note: This sends Ctrl-C (0x03) which should interrupt execution.
        """
        if not self.sock:
            return False
        try:
            self.sock.sendall(b"\x03")
            return True
        except socket.error:
            return False

    def kill(self) -> bool:
        """Kill the target process."""
        response = self._command("k")
        return response is not None

    def query_supported(self) -> Optional[str]:
        """Query supported features."""
        return self._command("qSupported")

    def query_attached(self) -> Optional[str]:
        """Query if attached to existing process."""
        return self._command("qAttached")

    def get_stop_reason(self) -> Optional[str]:
        """Get the reason the target stopped."""
        return self._command("?")

    def detach(self) -> bool:
        """Detach from the target."""
        response = self._command("D")
        return response == "OK"
