# SiFi Bridge Python

[![PyPI - Version](https://img.shields.io/pypi/v/sifi_bridge_py)](https://pypi.org/project/sifi-bridge-py/)
[![License](https://img.shields.io/github/license/SiFiLabs/sifi-bridge-py)](https://github.com/SiFiLabs/sifi-bridge-py/blob/main/LICENSE)

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

See [DEVELOPMENT.md](DEVELOPMENT.md) for full setup instructions.

```bash
export SIFIBRIDGE_EXE=./sifibridge  # point to a local binary
uv sync
```

## Deployment

**NOTE** If you add new enums or types, re-export them in `sifi_bridge_py/__init__.py`.

### Publishing `sifibridge-bin` (new CLI binary)

1. Update `version` in `sifibridge-bin/pyproject.toml`
2. Build wheels: `cd sifibridge-bin && python scripts/build_wheels.py <release-tag>`
3. Publish: `uv publish dist/*` or push a `bin-<version>` tag

### Publishing `sifi-bridge-py` (Python bindings)

1. Update `version` in `pyproject.toml` (and `sifibridge-bin` dependency pin if needed)
2. Run tests: `python -m unittest -v`
3. Push a version tag (e.g. `2.0.0-b9`) to `main` — CI handles the rest

`sifibridge-bin` must be on PyPI before publishing a `sifi-bridge-py` version that depends on it.
