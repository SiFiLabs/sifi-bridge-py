import os
import requests
import shutil
from platform import system
from importlib import metadata

from semantic_version import Version

import numpy as np

import logging


def get_sifi_bridge(version: str, output_dir: str):
    """
    Pull the SiFi Bridge CLI from the [official Github repository](https://github.com/SiFiLabs/sifi-bridge-pub).

    :param version: SiFi Bridge version triplet to pull. Use "latest" to pull the latest version.
    :param output_dir: Directory to save the executable to.

    :raises ValueError: If the version is not found or if `version` triplet is not valid.

    :return: Path to the downloaded executable.
    """
    # Find the latest upstream version
    if version == "latest":
        releases = requests.get(
            "https://api.github.com/repos/sifilabs/sifi-bridge-pub/releases",
            timeout=5,
        ).json()
        version = str(max([Version(release["tag_name"]) for release in releases]))
    else:
        # Make sure it's a valid version triplet
        Version(version)

    pltfm = system()
    extension = ".exe" if pltfm == "Windows" else ""
    executable = f"sifi_bridge-{version}-{pltfm.lower()}{extension}"
    arch = None
    if pltfm == "Linux":
        arch = "x86_64-unknown-linux-gnu"
        print(
            "Please run <chmod +x sifi_bridge> in the terminal to indicate this is an executable file! You only need to do this once."
        )
    elif pltfm == "Darwin":
        arch = "aarch64-apple-darwin"
    elif pltfm == "Windows":
        arch = "x86_64-pc-windows-gnu"

    # Get Github releases
    releases = requests.get(
        "https://api.github.com/repos/sifilabs/sifi-bridge-pub/releases",
        timeout=5,
    ).json()

    # Extract the release matching the requested version
    release_idx = [release["tag_name"] for release in releases].index(version)
    assets = releases[release_idx]["assets"]

    # Find the asset that matches the architecture
    archive_url = None
    for asset in assets:
        asset_name = asset["name"]
        if arch not in asset_name:
            continue
        archive_url = asset["browser_download_url"]
    if not archive_url:
        ValueError(f"No upstream version found for {executable}")

    # Fetch and write to disk as a zip file
    logging.info(f"Fetching sifi_bridge from {archive_url}")
    r = requests.get(archive_url)
    zip_path = "sifi_bridge" + ".zip" if pltfm == "Windows" else ".tar.gz"

    with open(zip_path, "wb") as file:
        file.write(r.content)

    # Unpack & delete the archive
    shutil.unpack_archive(zip_path, "./")
    os.remove(zip_path)
    extracted_path = f"sifi_bridge-{version}-{arch}/"
    for file in os.listdir(extracted_path):
        if not file.startswith("sifi_bridge"):
            continue
        shutil.move(extracted_path + file, f"{output_dir}/{executable}")
    shutil.rmtree(extracted_path)

    return f"{output_dir}/{executable}"


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


def get_package_version():
    """
    Get the version of the sifi_bridge_py package.

    The SiFi Bridge utilities follow semantic versioning.

    Consequently, the CLI and the Python package should always have the same major and minor versions to ensure compatibility.

    :return: Version string.
    """
    return metadata.version("sifi_bridge_py")
