# SiFi Bridge Python

[![PyPI - Version](https://img.shields.io/pypi/v/sifi_bridge_py)](https://pypi.org/project/sifi-bridge-py/)
[![License](https://img.shields.io/github/license/SiFiLabs/sifi-bridge-py)](https://github.com/SiFiLabs/sifi-bridge-py/blob/main/LICENSE)

A Python wrapper over the [SiFi Bridge CLI](https://github.com/SiFiLabs/sifi-bridge-pub) for talking to SiFi Labs devices (BioPoint, SiFiBand). Spawns `sifibridge` as a subprocess, drives it via its REPL, and delivers sensor data over a local TCP socket — so your code reads typed Python objects instead of parsing JSON lines.

## Installing

```bash
pip install sifi-bridge-py
```

The `sifibridge` CLI binary ships bundled per platform via the `sifibridge-bin` wheel; no separate install is needed on Linux (x86_64, aarch64), macOS (x86_64, arm64), or Windows (x86_64).

## Quickstart

```python
from sifi_bridge_py import SifiBridge

with SifiBridge() as sb:
    # Block until a device is found (sb.connect() returns False on no-match)
    while not sb.connect():
        pass

    sb.configure_sensors(emg=True)
    sb.configure_emg(fs=1000, mains_notch=60)

    sb.start()
    for _ in range(50):
        packet = sb.get_emg(timeout=2.0)
        if not packet:
            continue   # timed out, no data this tick
        print(packet["sample_rate"], packet["data"]["emg"][:4])
    sb.stop()
```

`SifiBridge` is a context manager — leaving the `with` block closes the data socket and shuts down the subprocess. If you can't use `with`, call `sb.close()` explicitly when done.

## API tour

**Lifecycle**

- `SifiBridge(use_lsl=False)` spawns the CLI; pass `use_lsl=True` to also stream data to Lab Streaming Layer.
- `sb.close()` (or `with` block exit) terminates the subprocess.

**Discovery & connection**

- `sb.list_devices(ListSources.BLE)` / `.SERIAL` / `.DEVICES` returns the names sifibridge can see.
- `sb.connect()` connects to any available device. Pass a `DeviceType`, a MAC address (Linux/Windows), or a CoreBluetooth UUID (macOS) to target a specific one. Returns `True` on success, `False` if no device matched in time (safe to retry in a loop).
- `sb.select_device(name)` / `sb.get_active_device()` / `sb.show()` introspect the current session.

**Sensor configuration**

- `sb.configure_sensors(ecg=…, emg=…, eda=…, imu=…, ppg=…)` toggles which sensors stream.
- `sb.configure_ecg(...)`, `configure_emg(...)`, `configure_eda(...)`, `configure_imu(...)`, `configure_ppg(...)` set per-sensor sampling rate, filtering, ranges, etc.
- `sb.set_onboard_filtering(enable)` turns the device's onboard filtering on/off globally.
- `sb.set_memory_mode(MemoryMode.STREAMING | DEVICE | BOTH)` controls whether data streams over BLE, lands on onboard flash, or both.

**Acquisition & data**

- `sb.start()` / `sb.stop()` toggle streaming.
- `sb.get_ecg(timeout=…)`, `get_emg`, `get_eda`, `get_imu`, `get_ppg`, `get_temperature` pop the next packet of that sensor. Each sensor has its own internal queue, so calling `get_ecg()` does **not** drop EMG data that arrived in between. Returns `{}` on timeout. **NOTE**: each packet is also routed to a generic queue read by `get_data()` — don't mix the two APIs on the same instance, or you'll see duplicates.
- `sb.clear_data_buffer()` drains all internal queues.

**Onboard memory**

- `sb.start_memory_download(timeout=10.0)` triggers a memory dump; pull `memory` packets via `get_data()` and check `sb.is_memory_download_completed(packet)`.
- `sb.buffer_export(fmt="csv"|"hdf5", output_dir=...)` writes buffered acquisitions to disk.
- `sb.erase_onboard_memory()` wipes the device's flash.

**Device controls** (no REPL escape hatch needed)

- `sb.set_led(index, on)`, `sb.set_motor(on)`, `sb.set_motor_intensity(level)`
- `sb.power_off()`, `sb.reset_to_default_config()`
- `sb.start_status_updates()` / `stop_status_updates()`
- `sb.get_memory_size()`, `sb.get_device_info()` — reply asynchronously as `status` packets on the data channel.
- `sb.set_ble_power(BleTxPower.LOW|MEDIUM|HIGH)`, `sb.set_night_mode(on)`, `sb.set_low_latency_mode(on)`

**Software events**

- `sb.send_event()` emits a software event on the device — useful for marking experiment timestamps.

## Error handling

```python
from sifi_bridge_py import SifiBridgeError, SifiBridgeTimeout
```

- `SifiBridgeTimeout` (subclass of `SifiBridgeError`) — the CLI didn't reply within the timeout. Retry-friendly. `connect()` already catches this internally and returns `False`.
- `SifiBridgeError` — the CLI returned an explicit `{"error": ...}` response. Indicates malformed input or an unsupported operation; fix the call rather than retrying.

Catch `SifiBridgeTimeout` specifically when you want to distinguish "still trying" from "broken":

```python
try:
    sb.configure_emg(fs=1000)
except SifiBridgeTimeout:
    # CLI is wedged — back off and retry
    ...
except SifiBridgeError as e:
    # Bad arguments — surface to the user
    raise
```

## Examples

Examples are available on our [documentation website](https://docs.sifilabs.com/sifi-bridge-py/examples).

## Advanced usage

The wrapper exposes the common surface, but the underlying CLI has more. To explore, run `sifibridge` interactively and type `help` — anything you find there can also be reached from Python by subclassing `SifiBridge` and calling `self._request("…")` directly. The REPL command reference is documented in [SiFiLabs/sifi-bridge-pub](https://github.com/SiFiLabs/sifi-bridge-pub).

## Tests

```bash
python -m unittest -v
```

Tests do not need a connected device. Some assertions exercise live REPL responses, so the bundled `sifibridge` binary must be runnable on your platform.

## Versioning

The wrapper is updated for every SiFi Bridge release. Major and minor versions are kept in lockstep with the CLI; patch versions vary for project-specific fixes.

## Local development

See [DEVELOPMENT.md](DEVELOPMENT.md) for setup. The short version:

```bash
export SIFIBRIDGE_EXE=./sifibridge   # point to a local CLI build
uv sync
```

## Deployment

**NOTE**: If you add new enums or types, re-export them in `sifi_bridge_py/__init__.py`.

### Publishing `sifibridge-bin`

1. Update `version` in `sifibridge-bin/pyproject.toml`
2. Build wheels: `cd sifibridge-bin && python scripts/build_wheels.py <release-tag>`
3. Publish: `uv publish dist/*` or push a `bin-<version>` tag

### Publishing `sifi-bridge-py`

1. Update `version` in `pyproject.toml` (and the `sifibridge-bin` pin if needed)
2. Run tests: `python -m unittest -v`
3. Push a version tag (e.g. `2.0.0-b9`) to `main` — CI handles the rest

`sifibridge-bin` must be on PyPI before publishing a `sifi-bridge-py` version that depends on it.
