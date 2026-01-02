"""
Dolphin emulator launcher with GDB stub support.
"""

import os
import subprocess
import time
import configparser
from pathlib import Path
from typing import Optional


class DolphinLauncher:
    """Manages Dolphin emulator lifecycle with GDB stub configuration."""

    # Default locations
    DEFAULT_DOLPHIN_APP = "/Applications/Dolphin.app"
    DEFAULT_CONFIG_DIR = Path.home() / "Library/Application Support/Dolphin"

    def __init__(
        self,
        dolphin_path: Optional[str] = None,
        config_dir: Optional[Path] = None,
        gdb_port: int = 9090,
    ):
        self.dolphin_path = dolphin_path or self.DEFAULT_DOLPHIN_APP
        self.config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.gdb_port = gdb_port
        self.process: Optional[subprocess.Popen] = None
        self._original_config: Optional[str] = None

    @property
    def dolphin_binary(self) -> str:
        """Path to the Dolphin executable."""
        if self.dolphin_path.endswith(".app"):
            return f"{self.dolphin_path}/Contents/MacOS/Dolphin"
        return self.dolphin_path

    @property
    def config_file(self) -> Path:
        """Path to Dolphin.ini."""
        return self.config_dir / "Config" / "Dolphin.ini"

    def _backup_config(self):
        """Backup the current config before modification."""
        if self.config_file.exists():
            self._original_config = self.config_file.read_text()

    def _restore_config(self):
        """Restore the original config."""
        if self._original_config is not None:
            self.config_file.write_text(self._original_config)
            self._original_config = None

    def configure_gdb_stub(self) -> bool:
        """
        Configure Dolphin to enable the GDB stub.

        Modifies Dolphin.ini to set GDBPort.
        """
        self._backup_config()

        config = configparser.ConfigParser()
        config.optionxform = str  # Preserve case

        if self.config_file.exists():
            config.read(self.config_file)

        if "Core" not in config:
            config["Core"] = {}

        config["Core"]["GDBPort"] = str(self.gdb_port)

        # Ensure parent directory exists
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.config_file, "w") as f:
            config.write(f)

        return True

    def disable_gdb_stub(self):
        """Disable the GDB stub by setting port to -1."""
        config = configparser.ConfigParser()
        config.optionxform = str

        if self.config_file.exists():
            config.read(self.config_file)

        if "Core" not in config:
            config["Core"] = {}

        config["Core"]["GDBPort"] = "-1"

        with open(self.config_file, "w") as f:
            config.write(f)

    def launch(
        self,
        iso_path: str,
        headless: bool = False,
        wait_for_gdb: bool = True,
        extra_args: Optional[list] = None,
    ) -> bool:
        """
        Launch Dolphin with the specified game.

        Args:
            iso_path: Path to the game ISO/GCM
            headless: Run in batch mode (no GUI)
            wait_for_gdb: If True, Dolphin will wait for GDB connection before starting
            extra_args: Additional command-line arguments

        Returns:
            True if launched successfully
        """
        if not Path(iso_path).exists():
            print(f"ISO not found: {iso_path}")
            return False

        if not Path(self.dolphin_binary).exists():
            print(f"Dolphin not found: {self.dolphin_binary}")
            return False

        # Configure GDB stub
        self.configure_gdb_stub()

        # Build command
        cmd = [self.dolphin_binary, "--exec", iso_path]

        if headless:
            cmd.append("--batch")

        if extra_args:
            cmd.extend(extra_args)

        # Launch
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"Launched Dolphin (PID: {self.process.pid})")
            print(f"GDB stub listening on port {self.gdb_port}")

            if wait_for_gdb:
                print("Dolphin is waiting for GDB connection...")

            return True

        except OSError as e:
            print(f"Failed to launch Dolphin: {e}")
            return False

    def wait_for_gdb_ready(self, timeout: float = 30.0) -> bool:
        """
        Wait for the GDB stub to be ready to accept connections.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if the stub is ready
        """
        import socket

        start = time.time()
        while time.time() - start < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                sock.connect(("localhost", self.gdb_port))
                sock.close()
                return True
            except (socket.error, socket.timeout):
                time.sleep(0.5)

        return False

    def stop(self):
        """Stop Dolphin if running."""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None

    def cleanup(self):
        """Stop Dolphin and restore original config."""
        self.stop()
        self._restore_config()

    def is_running(self) -> bool:
        """Check if Dolphin is still running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False


def find_melee_iso() -> Optional[str]:
    """
    Try to find a Melee ISO in common locations.

    Returns the path if found, None otherwise.
    """
    common_locations = [
        Path.home() / "Games",
        Path.home() / "ROMs",
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path("/Volumes"),
    ]

    patterns = ["*melee*.iso", "*melee*.gcm", "*GALE01*.iso", "*GALE01*.gcm"]

    for location in common_locations:
        if not location.exists():
            continue
        for pattern in patterns:
            matches = list(location.rglob(pattern))
            if matches:
                return str(matches[0])

    return None
