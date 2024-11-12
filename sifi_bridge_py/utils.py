import os
import requests
import shutil
from platform import system, machine
from importlib import metadata

from semantic_version import Version

import numpy as np

import logging

def _get_package_version():
    """
    Get the version of the `sifi_bridge_py` package.

    The SiFi Bridge utilities follow semantic versioning.

    Consequently, the CLI and the Python package should always have the same major and minor versions to ensure compatibility.

    :return str: Version string.
    """
    return metadata.version("sifi_bridge_py")

def _are__compatible(ver_1: str | Version, ver_2: str | Version) -> bool:
    """Check if two semantic verison-formatted strings are compatible (major and minor versions match).

    :return bool: True if compatible, False otherwise.
        
    """
    if not isinstance(ver_1, Version):
        ver_1 = Version(ver_1)
    if not isinstance(ver_2, Version):
        ver_2 = Version(ver_2)
    
    are_compatible = ver_1.major == ver_2.major and ver_1.minor == ver_2.minor
    
    if not are_compatible:
        logging.info(f"{ver_1} and {ver_2} are not compatible: major and minor version do not match.")
        
    return are_compatible
    
def _fetch_releases() -> list[dict]:
    """Fetch all SiFi Bridge releases from the official Github repository.
    """
    return requests.get(
            "https://api.github.com/repos/sifilabs/sifi-bridge-pub/releases",
            timeout=5,
        ).json()
    
def _get_latest_matching_version(releases: list[dict]) -> Version:
    sbp_version = Version(_get_package_version())
    versions = list(filter(lambda ver: _are__compatible(sbp_version, ver),[release["tag_name"] for release in releases]))
    return max(versions)

def _get_release_assets(releases, version) -> list[dict]:
    if not isinstance(version, str):
        version = str(version)
    release_idx = [release["tag_name"] for release in releases].index(version)
    assets = releases[release_idx]["assets"]
    return assets

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
        executable_path = f"{output_dir}{file}" if output_dir.endswith("/") else f"{output_dir}/{file}"
        # Overwrite executable
        if file in os.listdir(output_dir):
            os.remove(executable_path)
        shutil.move(f"{extracted_dir_name}/{file}", f"{output_dir}/")
        shutil.rmtree(extracted_dir_name)
        return executable_path

def get_sifi_bridge(output_dir: str):
    """
    Pull the latest compatible version of SiFi Bridge CLI from the [official Github repository](https://github.com/SiFiLabs/sifi-bridge-pub).

    :param output_dir: Directory to save the executable to.

    :raises AssertionError: If the version is not found or if `version` triplet is not valid.

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
    asset = _get_matching_asset(assets, arch, plftm)
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
