#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests",
# ]
# ///
"""Build platform-specific wheels for sifibridge-bin.

Downloads the sifibridge binary for each platform from a GitHub release,
packages it into a wheel, and renames the wheel with the correct platform tag.

Usage:
    python scripts/build_wheels.py <release_tag>

Example:
    python scripts/build_wheels.py 2.0.0-b8
"""

import shutil
import subprocess
import sys
from pathlib import Path

import requests

GITHUB_RELEASE_URL = (
    "https://api.github.com/repos/sifilabs/sifi-bridge-pub/releases/tags/{tag}"
)

# Maps GitHub asset target triple to wheel platform tag
PLATFORMS = {
    "x86_64-unknown-linux-gnu": "manylinux_2_17_x86_64.manylinux2014_x86_64",
    "aarch64-unknown-linux-gnu": "manylinux_2_17_aarch64.manylinux2014_aarch64",
    "x86_64-apple-darwin": "macosx_11_0_x86_64",
    "aarch64-apple-darwin": "macosx_11_0_arm64",
    "x86_64-pc-windows-msvc": "win_amd64",
}

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = ROOT / "sifibridge_bin" / "bin"
DIST_DIR = ROOT / "dist"


def fetch_release(tag: str) -> dict:
    r = requests.get(GITHUB_RELEASE_URL.format(tag=tag), timeout=10)
    r.raise_for_status()
    return r.json()


def find_asset(release: dict, target: str) -> dict:
    for asset in release["assets"]:
        if target in asset["name"]:
            return asset
    raise ValueError(f"No asset found for target '{target}' in release assets")


def download_and_extract(asset: dict, dest: Path):
    """Download an archive asset and extract the binary into dest."""
    url = asset["browser_download_url"]
    name = asset["name"]

    print(f"  Downloading {name}...")
    r = requests.get(url, timeout=60)
    r.raise_for_status()

    archive_path = dest / name
    archive_path.write_bytes(r.content)

    shutil.unpack_archive(str(archive_path), str(dest))
    archive_path.unlink()

    # The archive extracts to a directory; move the binary up into dest
    extracted_dir = name.replace(".zip", "").replace(".tar.gz", "")
    extracted_path = dest / extracted_dir
    for f in extracted_path.iterdir():
        if f.name.startswith("sifibridge"):
            shutil.move(str(f), str(dest / f.name))
    shutil.rmtree(extracted_path)


def clean_bin_dir():
    """Remove all files from bin/ except .gitkeep."""
    for f in BIN_DIR.iterdir():
        if f.name != ".gitkeep":
            f.unlink()


def build_wheel() -> Path:
    """Run uv build --wheel and return the path to the built wheel."""
    subprocess.run(["uv", "build", "--wheel"], cwd=ROOT, check=True)
    wheels = list((ROOT / "dist").glob("*.whl"))
    # Return the most recently created wheel
    return max(wheels, key=lambda p: p.stat().st_mtime)


def retag_wheel(wheel_path: Path, platform_tag: str) -> Path:
    """Rename a wheel file to replace the platform tag."""
    name = wheel_path.name
    # Wheel filename format: {name}-{version}-{python}-{abi}-{platform}.whl
    parts = name.rsplit("-", 1)  # Split off platform.whl
    new_name = f"{parts[0]}-{platform_tag}.whl"
    new_path = wheel_path.parent / new_name
    wheel_path.rename(new_path)
    return new_path


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <release_tag>")
        sys.exit(1)

    tag = sys.argv[1]
    print(f"Fetching release: {tag}")
    release = fetch_release(tag)

    # Clean dist directory
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir()

    final_wheels = []

    for target, platform_tag in PLATFORMS.items():
        print(f"\nBuilding wheel for {target}...")

        # Clean and download binary
        clean_bin_dir()
        asset = find_asset(release, target)
        download_and_extract(asset, BIN_DIR)

        # Build wheel
        wheel = build_wheel()
        print(f"  Built: {wheel.name}")

        # Retag with platform-specific tag
        retagged = retag_wheel(wheel, platform_tag)
        print(f"  Retagged: {retagged.name}")
        final_wheels.append(retagged)

    # Clean bin dir after building
    clean_bin_dir()

    print(f"\nDone! Built {len(final_wheels)} wheels in {DIST_DIR}/:")
    for w in final_wheels:
        print(f"  {w.name}")


if __name__ == "__main__":
    main()
