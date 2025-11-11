import os
import re
import requests
import shutil
from platform import system, machine
from importlib import metadata

from packaging.version import Version

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


def _fetch_releases() -> list[dict]:
    """Fetch all SiFi Bridge releases from the official Github repository."""
    return requests.get(
        "https://api.github.com/repos/sifilabs/sifi-bridge-pub/releases",
        timeout=5,
    ).json()


def _get_latest_matching_version(releases: list[dict]) -> str:
    """Get the latest release tag compatible with the current package version.

    The SiFi Bridge CLI uses SemVer (e.g., "v2.0.0-beta.1"), while the Python
    package uses PEP 440 (e.g., "2.0.0b1"). This function handles the conversion
    and returns the original tag name for use with GitHub releases.

    :param releases: List of release dictionaries from GitHub API
    :return: The tag name of the latest compatible release (e.g., "v2.0.0-beta.1")
    """
    sbp_version = Version(_get_package_version())

    # Parse all tag names and filter for compatible versions
    # Store tuples of (Version object, original tag name)
    compatible_versions = []
    for release in releases:
        tag = release["tag_name"]
        try:
            # Check if this tag is compatible with our package version
            if _are_compatible(sbp_version, tag):
                # Strip 'v' prefix if present for parsing
                version_str = tag[1:] if tag.startswith("v") else tag
                parsed_version = Version(version_str)
                compatible_versions.append((parsed_version, tag))
        except Exception:
            # Skip releases with unparseable version tags
            continue

    if not compatible_versions:
        raise ValueError(f"No compatible versions found for {sbp_version}")

    # Find the latest version and return its original tag name
    latest = max(compatible_versions, key=lambda x: x[0])
    return latest[1]  # Return the original tag name


def _get_release_assets(releases: list[dict], tag_name: str) -> list[dict]:
    """Get the assets for a specific release by its tag name.

    :param releases: List of release dictionaries from GitHub API
    :param tag_name: The exact tag name from GitHub (e.g., "v2.0.0-beta.1")
    :return: List of asset dictionaries for the release
    :raises ValueError: If the tag name is not found in releases
    """
    for release in releases:
        if release["tag_name"] == tag_name:
            return release["assets"]

    raise ValueError(f"Release with tag '{tag_name}' not found")


def _get_matching_asset(assets: list[dict], architecture: str, platform: str) -> dict:
    arch = architecture.lower()
    platform = platform.lower()

    for asset in assets:
        asset_name = asset["name"]
        if arch not in asset_name or platform not in asset_name:
            continue
        return asset
    raise ValueError(f"No asset found for {arch} on {platform}")


def _download_and_extract_sifibridge(archive: dict, output_dir: str) -> str:
    r = requests.get(archive["browser_download_url"])

    archive_name = archive["name"]
    with open(archive_name, "wb") as file:
        file.write(r.content)

    # Unpack & delete the archive
    # TODO safety checks?
    shutil.unpack_archive(archive_name, "./")
    os.remove(archive_name)

    # Remove zip/tar.gz extension
    extracted_dir_name = archive_name.replace(".zip", "").replace(".tar.gz", "")
    executable = archive["name"].split("-")[0]
    # Find the executable and move it to the current directory
    for file in os.listdir(extracted_dir_name):
        if not file.startswith(executable):
            continue
        executable_path = (
            f"{output_dir}{file}"
            if output_dir.endswith("/")
            else f"{output_dir}/{file}"
        )
        # Overwrite executable
        if file in os.listdir(output_dir):
            os.remove(executable_path)
        shutil.move(f"{extracted_dir_name}/{file}", f"{output_dir}/")
        shutil.rmtree(extracted_dir_name)
        return executable_path


def get_sifi_bridge(output_dir: str):
    """
    Pull the latest compatible version of SiFi Bridge CLI from the [official Github repository](https://github.com/SiFiLabs/sifi-bridge-pub).

    Automatically finds the latest CLI release with matching major and minor version
    numbers. The CLI uses SemVer format (e.g., "v2.0.0-beta.1") while this package
    uses PEP 440 (e.g., "2.0.0b1"), but both are semantically equivalent.

    :param output_dir: Directory to save the executable to.

    :raises AssertionError: If the output directory does not exist.
    :raises ValueError: If no compatible version is found or if the platform/architecture is not supported.

    :return: Path to the downloaded executable.
    """
    assert os.path.isdir(output_dir), f"Output directory {output_dir} does not exist."
    releases = _fetch_releases()
    ver = _get_latest_matching_version(releases)
    assets = _get_release_assets(releases, ver)
    arch, pltfm = machine().lower(), system().lower()
    if arch == "amd64":
        # Check for windows
        arch = "x86_64"
    elif arch == "arm64":
        arch = "aarch64"
    asset = _get_matching_asset(assets, arch, pltfm)
    exe = _download_and_extract_sifibridge(asset, output_dir)
    return exe


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
