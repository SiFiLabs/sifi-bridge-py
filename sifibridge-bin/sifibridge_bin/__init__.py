import os
import sys
import stat
from pathlib import Path

# Override with SIFIBRIDGE_EXE env var for development
_ENV_OVERRIDE = "SIFIBRIDGE_EXE"


def get_executable() -> str:
    """Return the path to the sifibridge binary.

    Checks in order:
    1. SIFIBRIDGE_EXE environment variable (for development)
    2. Bundled binary in the installed package
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        exe = Path(override)
        if not exe.exists():
            raise FileNotFoundError(
                f"{_ENV_OVERRIDE} set to {override} but file does not exist"
            )
        return str(exe)

    bin_dir = Path(__file__).parent / "bin"

    if sys.platform == "win32":
        exe = bin_dir / "sifibridge.exe"
    else:
        exe = bin_dir / "sifibridge"

    if not exe.exists():
        raise FileNotFoundError(
            f"sifibridge binary not found at {exe}. "
            "Your platform may not be supported, or the package was not installed correctly."
        )

    # Ensure executable permission on Unix
    if sys.platform != "win32":
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)

    return str(exe)
