# SiFi Bridge Python

[![PyPI - Version](https://img.shields.io/pypi/v/sifi_bridge_py)](https://pypi.org/project/sifi-bridge-py/)
[![License](https://img.shields.io/github/license/SiFiLabs/sifi-bridge-py)](https://github.com/SiFiLabs/sifi-bridge-py/blob/main/LICENSE)

A Python wrapper over the [SiFi Bridge CLI](https://github.com/SiFiLabs/sifi-bridge-pub) for talking to SiFi Labs devices (BioPoint, SiFiBand, SiFiBand Focus). Spawns `sifibridge` as a subprocess, drives it via its REPL, and delivers sensor data over a local TCP socket — so your code reads Python dicts instead of parsing JSON lines.

This release targets **SiFi Bridge 2.0.0** and is not compatible with 1.x. See [Migrating from 1.x](#migrating-from-1x).

## Installing

```bash
pip install sifi-bridge-py
```

The `sifibridge` CLI binary ships bundled per platform via the `sifibridge-bin` wheel; no separate install is needed on Linux (x86_64, aarch64), macOS (x86_64, arm64), or Windows (x86_64).

## Quickstart

```python
from sifi_bridge_py import SifiBridge

with SifiBridge() as sb:
    # connect() returns False on no-match, so it is safe to retry in a loop
    while not sb.connect():
        pass

    sb.configure_sensors(emg=True)
    sb.configure_emg(fs=1000, mains_notch=60)

    sb.start()
    for _ in range(50):
        packet = sb.get_emg(timeout=2.0)
        if not packet:
            continue   # timed out, no data this tick
        print(packet.get("sample_rate"), packet["data"]["emg"][:4])
    sb.stop()
```

`sample_rate` is measured live and takes two samples to establish, so it is
absent from the first packet of a stream rather than reported as zero — read it
with `.get()`. `samples_lost` is likewise omitted on a healthy packet.

`SifiBridge` is a context manager — leaving the `with` block closes the data socket and shuts down the subprocess. If you can't use `with`, call `sb.close()` explicitly when done.

## Configuration is incremental

The wrapper mirrors the CLI: sifibridge leaves a setting untouched when its flag is absent, so every `configure_*` parameter defaults to `None` and only the parameters you actually pass are sent. `configure_emg(fs=1000)` here does exactly what `configure emg --fs 1000` does in the REPL.

```python
sb.configure_emg(fs=2000, mains_notch=50, bandpass=True, flo=20, fhi=450)
sb.configure_emg(fs=1000)   # changes the rate; the filters above are untouched
```

The same applies to `configure_sensors`: an omitted sensor keeps its current state, so `configure_sensors(ppg=True)` enables PPG without disabling anything else. To turn a sensor off, say so explicitly (`configure_sensors(ppg=False)`).

The mains notch is the one tri-state parameter, because "leave it alone" and "switch it off" are different things:

```python
sb.configure_ecg(mains_notch=50)      # 50 Hz
sb.configure_ecg(mains_notch=60)      # 60 Hz
sb.configure_ecg(mains_notch="off")   # disable it (0 and False also work)
sb.configure_ecg(mains_notch=None)    # leave it however it is (the default)
```

## Targeting several devices

Every device command accepts `all=True` (apply to every connected device) or `devices=[...]` (apply to the named handles — a device ID or name):

```python
sb.configure_emg(fs=1000, all=True)
sb.start(devices=["left_arm", "right_arm"])
```

When you target more than one device, sifibridge answers with an aggregated response, so these calls return a **list of per-device responses** instead of the single-device value. `start()`, `stop()` and `send_event()` return `True`/`False` for one device and a list for several.

## API tour

**Lifecycle**

- `SifiBridge(use_lsl=False)` spawns the CLI; pass `use_lsl=True` to also stream data to Lab Streaming Layer.
- `sb.close()` (or `with` block exit) terminates the subprocess.

**Discovery & connection**

- `sb.list_devices(ListSources.BLE | .SERIAL | .DEVICES)` returns one dict per device (`id`, `name`, …).
- `sb.connect(handle=None)` connects to any available device, or to a specific BLE name, MAC address (Linux/Windows) or CoreBluetooth UUID (macOS). Returns `True` on success, `False` if nothing matched in time.
- `sb.disconnect()` closes the link and drops the session.
- `sb.select_device(handle)` switches which connected device subsequent commands target.
- `sb.rename_device(name)` gives the device a custom BLE name (max 14 bytes); `sb.rename_device(None)` resets it.

**Device information**

- `sb.info()` returns everything sifibridge knows about the active device. Almost all of it is nested under `configuration` — `info()` itself carries only `id`, `name`, `device` and `connected`.
- `sb.get_configuration()` returns that block; `sb.get_active_device()`, `sb.get_sensors()` (the hardware inventory), `sb.get_sensor_states()` (what is currently enabled), `sb.get_device_state()` and `sb.get_battery()` are shortcuts into it.

**Sensor configuration**

- `sb.configure_sensors(ecg=…, emg=…, eda=…, imu=…, ppg=…)` toggles which sensors stream.
- `sb.configure_ecg(...)`, `configure_emg(...)`, `configure_eda(...)`, `configure_imu(...)`, `configure_ppg(...)`, `configure_temperature(...)` set per-sensor sampling rate, filtering and ranges.
- `sb.set_onboard_filtering(enable)`, `sb.set_high_gain(enable)`, `sb.set_low_latency_mode(on)`, `sb.set_night_mode(on)`, `sb.set_ble_power(BleTxPower.LOW|MEDIUM|HIGH)`.
- `sb.set_memory_mode(MemoryMode.STREAMING | DEVICE | BOTH)` controls whether data streams over BLE, lands on onboard flash, or both.

PPG is configured through the two hardware primitives: `sps` (raw AFE rate) and `avg` (averaging factor). The effective output rate is `sps / avg`, and the wrapper caps it at **200 Hz**. Set either one on its own if you like — the wrapper reads the other back from the device to work out what the rate will be.

Temperature has no enable of its own and is not part of `configure_sensors`, but the device only emits it alongside an otherwise active acquisition, so at least one other sensor must be on.

**Acquisition & data**

- `sb.start(set_default=False)` / `sb.stop()` toggle streaming. `set_default=True` stores the current configuration as the device's power-on default.
- `sb.get_ecg(timeout=…)`, `get_emg`, `get_eda`, `get_imu`, `get_ppg`, `get_temperature`, `get_event` pop the next packet of that sensor. Each sensor has its own internal queue, so calling `get_ecg()` does **not** drop EMG data that arrived in between. Returns `{}` on timeout. **NOTE**: each packet is also routed to a generic queue read by `get_data()` — don't mix the two APIs on the same instance, or you'll see duplicates.
- `sb.clear_data_buffer()` drains all internal queues.
- `sb.send_event()` emits a software event, which arrives in the stream as an `event` packet timestamped on the device — useful for marking experiment landmarks.
- `sb.set_status_updates(on)` toggles the ~1 Hz status packets (battery, memory used, device state).

**Buffers & onboard memory**

2.0.0 routes all recorded data through a buffering subsystem; exporting to disk is a separate step.

- `sb.buffer_list()`, `sb.buffer_info()` inspect buffered acquisitions.
- `sb.buffer_pull(sensor, last_seconds=…)` pulls samples back into Python.
- `sb.buffer_export(fmt="csv"|"hdf5", output_dir=…)` writes them to disk.
- `sb.buffer_clear(all=True)` frees them.
- `sb.download_memory_ble(output_dir)` / `sb.download_memory_serial(port, output_dir)` pull the device's onboard flash into the buffers and export it in one call. These block; a full flash over BLE takes hours.
- `sb.erase_onboard_memory(format=False)` wipes the recordings; `format=True` fully formats the flash.

**Device controls**

- `sb.set_led(index, on)`, `sb.set_motor(on)`, `sb.set_motor_intensity(level)` (1–10)
- `sb.power_off()`
- `sb.dfu(package_path)` updates the device firmware over BLE.

> **Paths and names cannot contain spaces.** The sifibridge REPL splits command lines on whitespace and does not support quoting, so `output_dir`, the DFU package path, and device names/handles must be space-free. The wrapper raises `ValueError` up front rather than sending something the CLI would mis-parse. Export to a path without spaces and move the files afterwards.

## Timestamps

Sample timestamps are **relative to the start of the acquisition**, in seconds — the first sample of a stream is at `0.0`. The acquisition's Unix epoch start arrives once, on the `start_time` packet:

```python
from sifi_bridge_py.utils import get_start_time, absolute_timestamps

start = None
while start is None:
    packet = sb.get_data()
    if packet.get("packet_type") == "start_time":
        start = get_start_time(packet)

emg = sb.get_emg()
t = absolute_timestamps(emg, start)   # one Unix epoch timestamp per sample
```

Don't use a packet's `received_at` for this: that is when the host received the packet, including BLE transit and buffering, not a per-sample time.

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
except SifiBridgeError:
    # Bad arguments — surface to the user
    raise
```

## Migrating from 1.x

SiFi Bridge 2.0.0 reworked the REPL, so this is a breaking release. The changes you are most likely to hit:

| 1.x | 2.0.0 |
| --- | --- |
| `sb.show()` | `sb.info()` |
| `new` / `delete` device managers | gone — `connect()` creates the session, `disconnect()` removes it |
| `configure_channels(...)` | `configure_sensors(...)` |
| `configure_*` defaults overwrote every setting | omitted parameters are left untouched |
| `configure_imu(gyro_range=…)` | removed; the FIFO pins the gyro full scale per IMU part |
| `configure_imu(accel_range=2\|4\|8\|16)` | `8` or `16` only |
| `configure_ppg(iir=…, ired=…)` | `configure_ppg(ir=…, red=…)`, capped at `sps/avg ≤ 200 Hz` |
| `start_status_updates()` / `stop_status_updates()` | `set_status_updates(on)` |
| `start_memory_download()` + `memory` packets | `download_memory_ble()` / `download_memory_serial()` |
| CSV publisher wrote files as data arrived | record into buffers, then `buffer_export()` |
| `list_devices()` returned names | returns dicts with `id` and `name` |
| `BioPoint_v1_1` … `BioPoint_v1_3` device types | a single `BioPoint`; revisions are in `info()` |

Packet fields moved too: `data_lost_count` → `samples_lost`, and the packet's arrival time `timestamp` → `received_at`, with a new per-sample `timestamps` array (relative to the acquisition start, which is reported as `start_time`). The full list is in the [SiFi Bridge changelog](https://github.com/SiFiLabs/sifi-bridge-pub/blob/main/CHANGELOG.md).

## Examples

Examples are available on our [documentation website](https://docs.sifilabs.com/sifi-bridge-py/examples).

## Advanced usage

The wrapper exposes the common surface, but the underlying CLI has more. To explore, run `sifibridge -p` interactively and type `help` — anything you find there can also be reached from Python by subclassing `SifiBridge` and calling `self._request("…")` directly. The REPL command reference is documented at [docs.sifilabs.com/cli](https://docs.sifilabs.com/cli).

## Tests

The suite is in three tiers:

```bash
uv run python -m unittest tests.test_unit -v          # no binary, no hardware
uv run python -m unittest tests.test_integration -v   # spawns a real sifibridge
SIFI_HW=BioPoint uv run python -m unittest tests.test_hardware -v   # needs a device
```

Tier 1 and Tier 2 run in CI on every push. Tier 2 also feeds every command line the wrapper can generate to a real `sifibridge` and asserts it parses — the wrapper builds those lines as strings, so a renamed or removed CLI flag is otherwise invisible until runtime. Tier 3 requires a powered-on device and is gated on `SIFI_HW`.

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
2. Update `SIFIBRIDGE_VERSION` in `.github/workflows/tests.yml` if the targeted CLI release changed
3. Run the tests
4. Push a version tag (e.g. `2.0.0`) to `main` — CI handles the rest

`sifibridge-bin` must be on PyPI before publishing a `sifi-bridge-py` version that depends on it.
