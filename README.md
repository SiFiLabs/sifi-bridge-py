# SiFi Bridge Python

[![PyPI - Version](https://img.shields.io/pypi/v/sifi_bridge_py)](https://pypi.org/project/sifi-bridge-py/)
[![License](https://img.shields.io/github/license/SiFiLabs/sifi-bridge-py)](https://github.com/SiFiLabs/sifi-bridge-py/blob/main/LICENSE)
[![pdm-managed](https://img.shields.io/badge/pdm-managed-blueviolet)](https://pdm-project.org)

SiFi Bridge Python is a convenient wrapper over [SiFi Bridge CLI](https://github.com/SiFiLabs/sifi-bridge-pub).

The Python wrapper opens the CLI tool in a subprocess. Thus, it is highly recommended to implement threading, since reading from standard input is a blocking operation. To use the wrapper, start by instantiating a SiFiBridge() object. All relevant usage documentation is delivered via inline doc-strings.

## Documentation

Inline documentation is provided. Sphinx API documentation will be coming shortly.

## Examples

Examples are available in the `examples/` directory of this project.

## Installing

`pip install sifi_bridge_py` should work for most use cases.

## Versioning

The wrapper is updated for every SiFi Bridge version. Major and minor versions will always be kept in sync, while the patch version will vary for language-specific bug fixes.
