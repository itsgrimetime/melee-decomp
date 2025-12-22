"""Run formatting tools (clang-format)."""

import asyncio
from pathlib import Path


async def format_files(
    files: list[str],
    melee_root: Path
) -> bool:
    """Run git clang-format on files.

    This runs `git clang-format` on the specified files to ensure they
    conform to the project's coding style.

    Args:
        files: List of file paths relative to melee_root
        melee_root: Path to the melee project root

    Returns:
        True if successful, False otherwise
    """
    try:
        if not files:
            print("No files to format")
            return True

        # Convert relative paths to absolute paths
        abs_files = [str(melee_root / file_path) for file_path in files]

        # First, stage the files
        print(f"Staging files for formatting: {', '.join(files)}")
        stage_cmd = ["git", "add"] + abs_files

        process = await asyncio.create_subprocess_exec(
            *stage_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            print(f"Error staging files: {stderr.decode()}")
            return False

        # Run git clang-format
        print("Running git clang-format...")
        format_cmd = ["git", "clang-format"]

        process = await asyncio.create_subprocess_exec(
            *format_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            print(f"Error running git clang-format: {stderr.decode()}")
            return False

        output = stdout.decode()
        if output.strip():
            print(f"Formatting output: {output}")

        # Re-stage the formatted files
        print("Re-staging formatted files...")
        process = await asyncio.create_subprocess_exec(
            *stage_cmd,
            cwd=str(melee_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            print(f"Error re-staging files: {stderr.decode()}")
            return False

        print("Successfully formatted files")
        return True

    except FileNotFoundError:
        print("Error: git clang-format not found. Please ensure it's installed and in PATH")
        return False
    except Exception as e:
        print(f"Error formatting files: {e}")
        return False


async def verify_clang_format_available() -> bool:
    """Verify that git clang-format is available.

    Returns:
        True if available, False otherwise
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "clang-format", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            version = stdout.decode().strip()
            print(f"Found git clang-format: {version}")
            return True
        else:
            return False

    except FileNotFoundError:
        return False
    except Exception:
        return False
