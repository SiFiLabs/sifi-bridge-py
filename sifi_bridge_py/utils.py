import re

from packaging.version import Version
from importlib import metadata

import numpy as np


def _get_package_version():
    """
    Get the version of the `sifi_bridge_py` package.

    The SiFi Bridge CLI follows SemVer (e.g., "2.0.0-beta.1"), while the Python
    package follows PEP 440 (e.g., "2.0.0b1"). Both use semantic versioning principles.

    The CLI and the Python package should always have the same major and minor
    versions to ensure compatibility.

    :return str: Version string in PEP 440 format (e.g., "2.0.0b1").
    """
    return metadata.version("sifi_bridge_py")


def _are_compatible(ver_1: str | Version, ver_2: str | Version) -> bool:
    """Check if two PEP 440 version strings are compatible (major and minor versions match).

    Supports standard PEP 440 formats including pre-releases (e.g., 2.0.0b1, 2.0.0-beta.1).
    Includes fallback mechanism for v-prefixed versions (e.g., v1.2.3).

    :return bool: True if compatible, False otherwise.

    """

    def parse_version(ver):
        """Parse version with fallback for non-standard formats."""
        if isinstance(ver, Version):
            return ver

        ver_str = str(ver).strip()

        # Try standard parsing first (handles PEP 440 including beta, alpha, rc, etc.)
        try:
            return Version(ver_str)
        except Exception:
            pass

        # Fallback: try stripping common prefixes like 'v'
        if ver_str.startswith("v") or ver_str.startswith("V"):
            try:
                return Version(ver_str[1:])
            except Exception:
                pass

        # Fallback: manual parsing for major.minor extraction
        # Handle edge cases like: "v1.2.3-beta.1" after normalization fails
        match = re.match(r"^[vV]?(\d+)\.(\d+)", ver_str)
        if match:
            major, minor = int(match.group(1)), int(match.group(2))
            # Create a minimal Version object using just major.minor.0
            try:
                return Version(f"{major}.{minor}.0")
            except Exception:
                pass

        # If all else fails, raise an error
        raise ValueError(f"Unable to parse version: {ver}")

    ver_1 = parse_version(ver_1)
    ver_2 = parse_version(ver_2)

    # Use .release tuple which works for all PEP 440 versions
    # .release is a tuple like (2, 0, 0) for both "2.0.0" and "2.0.0b1"
    return ver_1.release[:2] == ver_2.release[:2]


def get_attitude_from_quats(qw, qx, qy, qz):
    """
    Calculate attitude from quaternions.

    :return: pitch, yaw, roll in radians.
    """
    quats = np.array([qw, qx, qy, qz]).reshape(4, -1)
    quats /= np.linalg.norm(quats, axis=0)
    qw, qx, qy, qz = quats
    yaw = np.arctan2(2.0 * (qy * qz + qw * qx), qw * qw - qx * qx - qy * qy + qz * qz)
    aasin = qx * qz - qw * qy
    pitch = np.arcsin(-2.0 * aasin)
    roll = np.arctan2(2.0 * (qx * qy + qw * qz), qw * qw + qx * qx - qy * qy - qz * qz)
    return pitch, yaw, roll
