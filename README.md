# SiFi Bridge Python

[![PyPI - Version](https://img.shields.io/pypi/v/sifi_bridge_py)](https://pypi.org/project/sifi-bridge-py/)
[![License](https://img.shields.io/github/license/SiFiLabs/sifi-bridge-py)](https://github.com/SiFiLabs/sifi-bridge-py/blob/main/LICENSE)
[![pdm-managed](https://img.shields.io/badge/pdm-managed-blueviolet)](https://pdm-project.org)

SiFi Bridge Python is a convenient wrapper over [SiFi Bridge CLI](https://github.com/SiFiLabs/sifi-bridge-pub).

The Python wrapper opens the CLI tool in a subprocess. Thus, it is highly recommended to implement threading, since reading from standard input is a blocking operation. To use the wrapper, start by instantiating a `SifiBridge` object. Documentation is provided as inline doc-strings. It is recommended to then deliver the samples with some sort of higher-level server-client scheme.

## Documentation

Inline documentation is provided.

## Examples

Examples are available in the `examples/` directory of this project.

## Tests

Tests are located under `tests/`. They can be ran with: `python -m unittest -v` from the root of the project's directory.

## Installing

`pip install sifi_bridge_py` should work for most use cases.

## Versioning

The wrapper is updated for every SiFi Bridge version. Major and minor versions will always be kept in sync, while the patch version will vary for project-specific bug fixes.

## Local development

`pip install path/to/sifi-bridge-py/`

## Deployment

**NOTE** If you add new enums or types to `sifi-bridge-py`, don't forget to re-export them in `src/__init__.py`.

First, run the tests: `python -m unittest -v`. Then, to deploy to PyPI, push a tag to the `main` branch. The tag must respect semantic versioning format: `x.y.z`.
