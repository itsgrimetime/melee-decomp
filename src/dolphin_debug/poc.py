#!/usr/bin/env python3
"""
Proof of Concept: Dolphin GDB stub debugging for Melee.

This script demonstrates:
1. Launching Dolphin with GDB stub enabled
2. Connecting via GDB Remote Serial Protocol
3. Reading memory from known Melee addresses

Usage:
    python -m src.dolphin_debug.poc --iso /path/to/melee.iso

Or for connection-only mode (if Dolphin is already running):
    python -m src.dolphin_debug.poc --connect-only
"""

import argparse
import sys
import time
from pathlib import Path

from .rsp_client import GDBClient
from .launcher import DolphinLauncher, find_melee_iso


# Known Melee memory addresses (NTSC 1.02 - GALE01)
# Reference: https://github.com/project-slippi/slippi-ssbm-asm
MELEE_ADDRESSES = {
    # Game state
    "game_state": 0x80479D60,  # Current game state
    "frame_counter": 0x80479D60 + 0x4,  # Frame counter
    "stage_id": 0x8049E6C8,  # Current stage ID
    # Match state
    "match_info": 0x8046B6A0,  # Match info struct
    # Player data (P1)
    "p1_stocks": 0x80453F9E,  # P1 stock count
    "p1_percent": 0x80453F9C,  # P1 damage percent
    "p1_pos_x": 0x80453090,  # P1 X position (float)
    "p1_pos_y": 0x80453094,  # P1 Y position (float)
    "p1_action_state": 0x80452C70,  # P1 action state
    # Static data
    "game_id": 0x80000000,  # Game ID string (should be "GALE01")
    "version": 0x80000007,  # Version byte
}


def test_memory_read(client: GDBClient) -> bool:
    """Test reading various memory locations."""
    print("\n=== Memory Read Tests ===\n")

    # Test 1: Read game ID (should be "GALE" for Melee NTSC)
    print("1. Reading Game ID at 0x80000000...")
    game_id = client.read_memory(0x80000000, 6)
    if game_id:
        try:
            game_id_str = game_id.decode("ascii")
            print(f"   Game ID: {game_id_str}")
            if game_id_str == "GALE01":
                print("   ✓ Confirmed: Super Smash Bros. Melee (NTSC 1.02)")
            else:
                print(f"   Note: Expected 'GALE01', got '{game_id_str}'")
        except UnicodeDecodeError:
            print(f"   Raw bytes: {game_id.hex()}")
    else:
        print("   ✗ Failed to read game ID")
        return False

    # Test 2: Read some memory near the start
    print("\n2. Reading memory region 0x80000000-0x80000020...")
    data = client.read_memory(0x80000000, 32)
    if data:
        print(f"   Hex: {data.hex()}")
        print(f"   ASCII: {data.decode('ascii', errors='replace')}")
    else:
        print("   ✗ Failed to read memory")

    # Test 3: Read a 32-bit value
    print("\n3. Testing 32-bit read...")
    val = client.read_u32(0x80000000)
    if val is not None:
        print(f"   Value at 0x80000000: 0x{val:08X}")
    else:
        print("   ✗ Failed to read u32")

    # Test 4: Try to read game state (might not be initialized if not in-game)
    print("\n4. Reading frame counter (may be 0 if not in-game)...")
    frame = client.read_u32(MELEE_ADDRESSES["frame_counter"])
    if frame is not None:
        print(f"   Frame: {frame}")
    else:
        print("   ✗ Failed to read frame counter")

    return True


def test_breakpoints(client: GDBClient) -> bool:
    """Test breakpoint functionality."""
    print("\n=== Breakpoint Tests ===\n")

    # Set a breakpoint at a known function
    # Using a safe location that won't break anything critical
    test_addr = 0x80006000  # Arbitrary address for testing

    print(f"1. Setting breakpoint at 0x{test_addr:08X}...")
    if client.set_breakpoint(test_addr):
        print("   ✓ Breakpoint set")

        print(f"2. Removing breakpoint at 0x{test_addr:08X}...")
        if client.remove_breakpoint(test_addr):
            print("   ✓ Breakpoint removed")
        else:
            print("   ✗ Failed to remove breakpoint")
    else:
        print("   ✗ Failed to set breakpoint (may not be supported)")

    return True


def test_registers(client: GDBClient) -> bool:
    """Test register reading."""
    print("\n=== Register Tests ===\n")

    print("1. Reading CPU registers...")
    regs = client.read_registers()
    if regs and "gpr" in regs:
        print("   General Purpose Registers (GPR 0-7):")
        for i in range(min(8, len(regs["gpr"]))):
            print(f"      r{i}: 0x{regs['gpr'][i]:08X}")
        print(f"   ... ({len(regs['gpr'])} total GPRs)")
    else:
        print("   ✗ Failed to read registers")

    return True


def interactive_mode(client: GDBClient):
    """Simple interactive mode for manual testing."""
    print("\n=== Interactive Mode ===")
    print("Commands:")
    print("  r <addr> [len]  - Read memory (hex address, optional length)")
    print("  w <addr> <val>  - Write u32 (hex address, hex value)")
    print("  b <addr>        - Set breakpoint")
    print("  d <addr>        - Delete breakpoint")
    print("  c               - Continue execution")
    print("  s               - Step one instruction")
    print("  regs            - Show registers")
    print("  q               - Quit")
    print()

    while True:
        try:
            line = input("gdb> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd == "q" or cmd == "quit":
                break
            elif cmd == "r" or cmd == "read":
                addr = int(parts[1], 16)
                length = int(parts[2], 16) if len(parts) > 2 else 16
                data = client.read_memory(addr, length)
                if data:
                    print(f"  {data.hex()}")
                else:
                    print("  (read failed)")
            elif cmd == "w" or cmd == "write":
                addr = int(parts[1], 16)
                val = int(parts[2], 16)
                if client.write_u32(addr, val):
                    print("  OK")
                else:
                    print("  (write failed)")
            elif cmd == "b" or cmd == "break":
                addr = int(parts[1], 16)
                if client.set_breakpoint(addr):
                    print(f"  Breakpoint set at 0x{addr:08X}")
                else:
                    print("  (failed)")
            elif cmd == "d" or cmd == "delete":
                addr = int(parts[1], 16)
                if client.remove_breakpoint(addr):
                    print(f"  Breakpoint removed at 0x{addr:08X}")
                else:
                    print("  (failed)")
            elif cmd == "c" or cmd == "continue":
                print("  Continuing... (Ctrl+C to interrupt)")
                result = client.continue_execution()
                print(f"  Stopped: {result}")
            elif cmd == "s" or cmd == "step":
                result = client.step()
                print(f"  Stopped: {result}")
            elif cmd == "regs" or cmd == "registers":
                regs = client.read_registers()
                if regs and "gpr" in regs:
                    for i in range(0, 32, 4):
                        row = "  "
                        for j in range(4):
                            if i + j < len(regs["gpr"]):
                                row += f"r{i+j:2d}=0x{regs['gpr'][i+j]:08X}  "
                        print(row)
                else:
                    print("  (failed to read registers)")
            else:
                print(f"  Unknown command: {cmd}")
        except (IndexError, ValueError) as e:
            print(f"  Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Dolphin GDB stub debugging POC for Melee"
    )
    parser.add_argument("--iso", help="Path to Melee ISO/GCM file")
    parser.add_argument(
        "--connect-only",
        action="store_true",
        help="Only connect to existing Dolphin instance",
    )
    parser.add_argument(
        "--port", type=int, default=9090, help="GDB stub port (default: 9090)"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run Dolphin in headless mode"
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Enter interactive mode"
    )
    args = parser.parse_args()

    launcher = None
    client = GDBClient(port=args.port)

    try:
        if not args.connect_only:
            # Find or use specified ISO
            iso_path = args.iso or find_melee_iso()
            if not iso_path:
                print("Error: No ISO specified and couldn't find Melee ISO")
                print("Please specify with --iso /path/to/melee.iso")
                return 1

            print(f"Using ISO: {iso_path}")

            # Launch Dolphin
            launcher = DolphinLauncher(gdb_port=args.port)
            if not launcher.launch(iso_path, headless=args.headless):
                print("Failed to launch Dolphin")
                return 1

            # Wait for GDB stub to be ready
            print("Waiting for GDB stub to be ready...")
            if not launcher.wait_for_gdb_ready(timeout=30.0):
                print("Timeout waiting for GDB stub")
                return 1

            print("GDB stub is ready")
            time.sleep(1)  # Give it a moment to fully initialize

        # Connect to GDB stub
        print(f"\nConnecting to GDB stub on port {args.port}...")
        if not client.connect():
            print("Failed to connect to GDB stub")
            print("\nMake sure Dolphin is running with GDB stub enabled.")
            print("You may need to:")
            print(f"  1. Add 'GDBPort = {args.port}' to [Core] section of Dolphin.ini")
            print("  2. Launch a game in Dolphin")
            return 1

        print("Connected!")

        # Query supported features
        supported = client.query_supported()
        if supported:
            print(f"Supported features: {supported[:100]}...")

        # Get stop reason
        stop_reason = client.get_stop_reason()
        print(f"Stop reason: {stop_reason}")

        # Run tests
        test_memory_read(client)
        test_registers(client)
        test_breakpoints(client)

        # Interactive mode if requested
        if args.interactive:
            interactive_mode(client)

        print("\n=== POC Complete ===")
        print("The GDB stub connection works! Ready to build the full CLI.")

    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        client.disconnect()
        if launcher:
            # Ask before stopping
            if not args.headless:
                try:
                    response = input("\nStop Dolphin? [y/N] ")
                    if response.lower() == "y":
                        launcher.cleanup()
                    else:
                        print("Leaving Dolphin running. GDB stub still available.")
                        launcher.disable_gdb_stub()  # Clean up config at least
                except (EOFError, KeyboardInterrupt):
                    pass
            else:
                launcher.cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())
