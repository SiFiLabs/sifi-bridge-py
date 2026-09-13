from __future__ import annotations

import subprocess as sp
import json
import socket
import time
from enum import Enum
from typing import Sequence
import threading
import queue

import logging

# Re-exported: these used to live here, and `from sifi_bridge_py.sifi_bridge
# import PacketType` must keep working.
from .packets import (  # noqa: F401
    BioChannel,
    DataPacket,
    DeviceType,
    PacketStatus,
    PacketType,
    SensorChannel,
)

logger = logging.getLogger(__name__)


class SifiBridgeError(RuntimeError):
    """
    Raised when sifibridge returns an `error` response for a command.

    Starting with sifibridge 2.0.0, every REPL command (except `list`) either
    returns its own response object as a top-level key (e.g., `{"connect": ...}`)
    on success, or `{"error": {"message": "..."}}` on failure. This exception
    surfaces the latter case.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SifiBridgeTimeout(SifiBridgeError):
    """
    Raised when sifibridge does not respond to a REPL command within the
    timeout. Subclasses `SifiBridgeError` so existing catches still work, but
    callers that want to distinguish "operation in progress / no device matched"
    from a protocol-level error can catch `SifiBridgeTimeout` specifically.
    """


class BleTxPower(Enum):
    """
    Use in tandem with SifiBridge.set_ble_power() to set the BLE transmission power.

    Higher transmission power will increase power consumption, but may improve connection stability.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryMode(Enum):
    """
    Sets how the device stores the data.

    - `STREAMING` streams data to the host computer via BLE
    - `DEVICE` saves the data stream to on-board flash
    - `BOTH` does both

    **NOTE**: SiFiBand does not support on-board memory,
    so this parameter is simply ignored.
    """

    STREAMING = "streaming"
    DEVICE = "device"
    BOTH = "both"


class PpgSensitivity(Enum):
    """
    Used to set the PPG light sensor sensitivity.

    Higher sensitivity in useful in cases where the PPG signal is weak, but may introduce noise or saturate the sensor.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class ListSources(Enum):
    """
    Use in tandem with SifiBridge.list_devices() to list devices from different sources.
    """

    BLE = "ble"
    SERIAL = "serial"
    DEVICES = "devices"


class SifiBridge:
    """
    Wrapper over the `sifibridge` CLI.

    Stdout is used as a request/reply channel for REPL commands.
    Sensor data is streamed separately over a local TCP socket: sifibridge
    binds `--tcp-out` as a server and we connect to it as a client.
    """

    _bridge: sp.Popen[bytes]
    """SiFi Bridge subprocess."""

    _response_queue: queue.Queue
    """Parsed JSON lines from stdout (one per command response)."""

    _data_queue: queue.Queue
    """All parsed sensor packets — backs `get_data()`."""

    _typed_queues: dict[str, queue.Queue]
    """Per-sensor queues — back `get_ecg()`, `get_emg()`, etc."""

    _response_lock: threading.Lock
    """Serializes (write to stdin) → (read from response queue) pairs."""

    _DEFAULT_REQUEST_TIMEOUT: float = 5.0
    """Default per-command timeout. sifibridge should reply within ms locally; the timeout exists only so misuse (no device, etc.) doesn't hang forever."""

    _DATA_SOCK_CONNECT_TIMEOUT: float = 10.0
    """How long __init__ waits for sifibridge to start listening on the data socket."""

    _closed: bool = False
    """Set by close() to make teardown idempotent."""

    _PACKET_TYPE_TO_SENSOR: dict[str, str] = {
        "ecg": "ecg",
        "emg": "emg",
        "emg_armband": "emg",
        "eda": "eda",
        "imu": "imu",
        "ppg": "ppg",
        "temperature": "temperature",
        "event": "event",
    }
    """Maps raw packet_type values to the sensor name used for typed queues.
    `emg` and `emg_armband` share a single queue because callers of `get_emg()`
    treat them interchangeably (BioPoint vs SiFiBand)."""

    def __init__(
        self,
        use_lsl: bool = False,
    ):
        """
        Spawn a sifibridge subprocess and connect to its data channel.

        :param use_lsl: If True, pass `--lsl` so sifibridge also streams data
            to Lab Streaming Layer outlets.
        """
        from sifibridge_bin import get_executable

        executable = get_executable()

        # sifibridge binds `--tcp-out` as a TCP server for the data stream and
        # we connect to it as a client. Pick a free localhost port for it to
        # bind: grab an ephemeral port from the OS, release it, and hand it to
        # sifibridge. There is a small race between releasing the port and
        # sifibridge binding it, but it is negligible on localhost.
        host = "127.0.0.1"
        port = self._find_free_port(host)

        exec_command = [
            executable,
            "--no-stdout-data",
            "--tcp-out",
            f"{host}:{port}",
        ]

        verbosity = self._verbosity_flag(logger.getEffectiveLevel())
        if verbosity:
            exec_command.append(verbosity)

        if use_lsl:
            exec_command.append("--lsl")

        logger.info(f"Launching executable: {' '.join(exec_command)}")
        self._bridge = sp.Popen(exec_command, stdin=sp.PIPE, stdout=sp.PIPE)

        self._data_sock = self._connect_data_socket(host, port)

        self._response_queue = queue.Queue()
        self._data_queue = queue.Queue()
        self._typed_queues = {
            sensor: queue.Queue()
            for sensor in set(self._PACKET_TYPE_TO_SENSOR.values())
        }
        self._response_lock = threading.Lock()

        threading.Thread(target=self._response_worker, daemon=True).start()
        threading.Thread(target=self._data_worker, daemon=True).start()

    @staticmethod
    def _verbosity_flag(level: int) -> str:
        """Translate a Python logging level into a sifibridge verbosity flag.

        sifibridge (via clap-verbosity-flag) logs at Error with no flag and
        raises one level per `-v`: `-v`=Warn, `-vv`=Info, `-vvv`=Debug. We
        match the wrapper's own logger so sifibridge is as chatty as we are.
        Returns an empty string for Error/Critical, i.e. sifibridge's default.
        """
        if level <= logging.DEBUG:
            return "-vvv"
        if level <= logging.INFO:
            return "-vv"
        if level <= logging.WARNING:
            return "-v"
        return ""

    @staticmethod
    def _find_free_port(host: str) -> int:
        """Reserve and immediately release an ephemeral port, returning it."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            return s.getsockname()[1]

    def _connect_data_socket(self, host: str, port: int) -> socket.socket:
        """
        Connect to sifibridge's data server, retrying until it is accepting.

        sifibridge binds `--tcp-out` asynchronously after startup, so the first
        connection attempts may be refused. Retry until success or until
        `_DATA_SOCK_CONNECT_TIMEOUT` elapses, bailing early if the subprocess
        dies.
        """
        deadline = time.monotonic() + self._DATA_SOCK_CONNECT_TIMEOUT
        while True:
            if self._bridge.poll() is not None:
                raise RuntimeError(
                    f"sifibridge exited (code {self._bridge.returncode}) before "
                    f"its data server at {host}:{port} became available"
                )
            try:
                sock = socket.create_connection((host, port))
                return sock
            except (ConnectionRefusedError, OSError):
                if time.monotonic() >= deadline:
                    self._bridge.kill()
                    raise RuntimeError(
                        f"Could not connect to sifibridge data server at "
                        f"{host}:{port} within {self._DATA_SOCK_CONNECT_TIMEOUT}s"
                    )
                time.sleep(0.05)

    def info(self) -> dict:
        """
        Get information about the active SiFi Bridge device.

        :raises SifiBridgeError: If no device is currently active. sifibridge
            replies with an explicit error in that case; the request timeout is
            only a safety net so a wedged subprocess can't block forever.
        """
        return self._request("info")["info"]

    def get_active_device(self) -> str:
        """
        :returns: Active device ID.
        :raises SifiBridgeError: If no device is currently active.
        """
        return self.info()["id"]

    def get_configuration(self) -> dict:
        """
        The active device's current configuration.

        Useful to confirm what a partial `configure_*` call left untouched:
        every configuration parameter defaults to None, meaning "leave as-is".

        Note that sifibridge nests almost everything it reports about a device
        in here — the sensor inventory, per-sensor settings, battery level and
        working state included. `info()` itself carries only `id`, `name`,
        `device`, `connected` and this block.

        :return: The ``configuration`` block of `info()`.
        :raises SifiBridgeError: If no device is currently active.
        """
        return self.info().get("configuration", {})

    def get_sensors(self) -> dict:
        """
        Which sensors the connected device physically has.

        This is the hardware inventory, not what is currently streaming: a
        sensor reads True here whether or not `configure_sensors` has it
        enabled. Use `get_sensor_states()` for the enabled/disabled picture.

        :return: A mapping like ``{"ecg": True, "emg": True, ...}``. Empty if
            the device's firmware does not report the field.
        :raises SifiBridgeError: If no device is currently active.
        """
        return self.get_configuration().get("sensors", {})

    def get_sensor_states(self) -> dict:
        """
        Which sensors are currently enabled for acquisition.

        :return: A mapping like ``{"ecg": True, "emg": False, ...}``, covering
            the sensors the device reports an ``enabled`` flag for. This is the
            state `configure_sensors` manipulates.
        :raises SifiBridgeError: If no device is currently active.
        """
        config = self.get_configuration()
        states = {}
        for sensor in ("ecg", "emg", "eda", "imu", "ppg"):
            block = config.get(sensor)
            if isinstance(block, dict) and "enabled" in block:
                states[sensor] = block["enabled"]
        return states

    def get_device_state(self) -> str | None:
        """
        The device's working state, as also reported on Status packets.

        :return: The state string, or None if the device does not report one.
        :raises SifiBridgeError: If no device is currently active.
        """
        return self.get_configuration().get("device_state")

    def get_battery(self) -> int | None:
        """
        The device's battery charge, in percent.

        :return: The charge level, or None if the device does not report one.
        :raises SifiBridgeError: If no device is currently active.
        """
        return self.get_configuration().get("battery_%")

    def select_device(self, name: str) -> str:
        """
        Select a device by ID or BLE local name.

        :raises ValueError: If `name` contains whitespace or ``;``.
        :raises SifiBridgeError: If no device matches `name`.

        :return: Active device ID
        """
        self._check_repl_token(name, "Device name")
        self._request(f"select {name}")
        return self.get_active_device()

    def rename_device(self, name: str | None = None) -> dict:
        """
        Rename the active device. Pass `None` to reset the device's name to
        its factory default.

        :param name: New name, at most 14 bytes (the firmware's limit), with no
            whitespace or ``;``.

        :raises ValueError: If `name` contains whitespace or ``;``, or exceeds
            14 bytes.
        :raises SifiBridgeError: If no device is currently connected.

        :return: `rename` response payload.
        """
        if name is not None:
            self._check_repl_token(name, "Device name")
            encoded = len(name.encode("utf-8"))
            if encoded > 14:
                raise ValueError(
                    f"Device name must be at most 14 bytes, got {encoded} ({name!r})"
                )
        cmd = "rename --reset" if name is None else f"rename {name}"
        return self._request(cmd)["rename"]

    def list_devices(self, source: ListSources | str) -> list[dict]:
        """
        List all devices found from a given `source`.

        :return: One dict per device. Entries carry at least ``id`` and
            ``name``; sifibridge 1.x returned bare name strings here.
        """
        if isinstance(source, str):
            source = ListSources(source)
        resp = self._request(f"list {source.value}")
        return resp["list"]["devices"]

    def connect(self, handle: str | None = None, timeout: float = 10.0) -> bool:
        """
        Try to connect to `handle`. A successful connect both creates a session
        and selects it as the active device.

        :param handle: Device handle to connect to. Can be:

            - `None` to auto-connect
            - the device's name
            - a MAC (Windows/Linux) / UUID (MacOS) to connect to a specific device.
        :param timeout: Connection timeout

        :return: True if connected, False if connection failed
        :raises ValueError: If `handle` contains whitespace or ``;``.
        :raises SifiBridgeError: sifibridge returned an error.
        """
        if handle is not None:
            self._check_repl_token(handle, "Device handle")
        try:
            resp = self._request(
                f"connect {handle if handle is not None else ''}", timeout
            )
        except SifiBridgeTimeout as e:
            logger.warning(f"Could not connect to {handle}: {e.message}")
            return False
        return resp["connect"]["connected"]

    def disconnect(self) -> bool:
        """
        Disconnect from the active device. Also removes its session; buffered
        acquisitions for the device are retained until `buffer clear`.

        :return: Connection status response (False after a successful disconnect).
        :raises SifiBridgeError: if sifibridge returns an error.
        """
        return self._request("disconnect")["disconnect"]["connected"]

    # ------------------------------------------------------------------
    # Command-building helpers
    # ------------------------------------------------------------------

    _PPG_SPS_CHOICES: tuple = (50, 100, 200, 400, 800)
    """Raw AFE sample rates the PPG front end accepts."""

    _PPG_AVG_CHOICES: tuple = (1, 2, 4, 8, 16, 32)
    """Averaging factors the PPG front end accepts."""

    _PPG_MAX_EFFECTIVE_RATE_HZ: float = 200.0
    """Cap the wrapper enforces on the PPG effective output rate (``sps / avg``).

    sifibridge itself allows up to 800 Hz, but rates above this are not usable
    over BLE in practice: the stream falls behind and packets start reporting
    ``samples_lost``. The wrapper refuses the configuration up front rather
    than letting a caller discover it from lossy data."""

    @staticmethod
    def _check_repl_token(value: str, what: str) -> str:
        """Reject a value the sifibridge REPL cannot carry in a command line.

        The 2.0.0 REPL splits each line on whitespace and does **not** support
        quoting, and it treats ``;`` as a command separator. A value containing
        either would be silently mis-parsed (or, worse, split into a second
        command), so the wrapper refuses it with an explanation instead of
        sending it.
        """
        if any(c.isspace() for c in value):
            raise ValueError(
                f"{what} must not contain whitespace ({value!r}): the sifibridge "
                "REPL splits commands on whitespace and does not support quoting"
            )
        if ";" in value:
            raise ValueError(
                f"{what} must not contain ';' ({value!r}): the sifibridge REPL "
                "treats ';' as a command separator"
            )
        return value

    @staticmethod
    def _join(*parts: str) -> str:
        """Join command fragments, dropping the empty ones."""
        return " ".join(p for p in parts if p)

    @staticmethod
    def _flag(name: str, value) -> str:
        """Render ``--name value``, or the empty string when `value` is None.

        This is what makes every configuration parameter optional: sifibridge
        2.0.0 leaves a setting untouched when its flag is absent, so a None
        parameter must produce no flag at all rather than a default value.

        Booleans render as sifibridge's ``on``/``off``, and floats use ``:g``
        so ``1.0`` becomes ``1`` (clap rejects ``1.0`` where it expects ``1``).
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            value = "on" if value else "off"
        elif isinstance(value, float):
            value = f"{value:g}"
        elif isinstance(value, Enum):
            value = value.value
        return f"--{name} {value}"

    @classmethod
    def _targets(
        cls,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ) -> str:
        """Render the device-targeting flags shared by sifibridge 2.0.0 commands.

        :param all: Apply to every managed device (``--all``).
        :param devices: A device handle, or a sequence of them, to target
            (``--devices a,b``). A handle is a device ID or name.
        :raises ValueError: If both `all` and `devices` are given, or a handle
            contains whitespace, ``,`` or ``;``.
        """
        if all and devices:
            raise ValueError("Pass either all=True or devices=..., not both")
        if all:
            return "--all"
        if not devices:
            return ""
        handles = [devices] if isinstance(devices, str) else list(devices)
        for handle in handles:
            cls._check_repl_token(handle, "Device handle")
            if "," in handle:
                raise ValueError(
                    f"Device handle must not contain ',' ({handle!r}): it "
                    "separates handles in --devices"
                )
        return f"--devices {','.join(handles)}"

    @staticmethod
    def _mains_notch_flag(value) -> str:
        """Render ``--mains-notch``, which is tri-state in sifibridge 2.0.0.

        None leaves the setting as-is (no flag), ``"off"``/``0``/``False``
        disables the filter, and ``50``/``60`` selects the mains frequency.
        """
        if value is None:
            return ""
        if value is False or value == 0 or str(value).lower() == "off":
            return "--mains-notch off"
        if value in (50, 60, "50", "60"):
            return f"--mains-notch {value}"
        raise ValueError(
            "mains_notch must be 50, 60, 'off'/0/False to disable, or None to "
            f"leave unchanged (got {value!r})"
        )

    def _multi_or(self, resp: dict, key: str):
        """Unwrap a command response.

        A command targeting several devices (``--all`` / ``--devices``) answers
        with an aggregated ``{"multi": {"responses": [...]}}`` instead of its
        own key, so unwrapping `key` unconditionally raises `KeyError` on every
        multi-device call.

        :return: The list of per-device responses for a multi response, else
            the command's own payload.
        """
        if "multi" in resp:
            return resp["multi"]["responses"]
        return resp[key]

    def _targeted(
        self,
        cmd: str,
        key: str,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
        timeout: float | None = None,
    ):
        """Send a targetable device command and unwrap its response."""
        line = self._join(cmd, self._targets(all, devices))
        return self._multi_or(self._request(line, timeout), key)

    def _configure(
        self,
        tail: str,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Send a ``configure`` command.

        ``configure`` takes its targeting flags **before** the subcommand
        (``configure --all ecg --fs 1000``); placing them after the subcommand
        is rejected by the binary.
        """
        line = self._join("configure", self._targets(all, devices), tail)
        return self._multi_or(self._request(line), "configure")

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure_sensors(
        self,
        ecg: bool | None = None,
        emg: bool | None = None,
        eda: bool | None = None,
        imu: bool | None = None,
        ppg: bool | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Enable or disable sensors.

        Every parameter is optional: a sensor left as None keeps its current
        state, so `configure_sensors(ppg=True)` enables PPG without disturbing
        the others.

        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :return: The ``configure`` payload, or a list of per-device responses
            when targeting several devices.
        """
        tail = self._join(
            "sensors",
            self._flag("ecg", ecg),
            self._flag("emg", emg),
            self._flag("eda", eda),
            self._flag("imu", imu),
            self._flag("ppg", ppg),
        )
        return self._configure(tail, all, devices)

    def configure_ecg(
        self,
        fs: int | None = None,
        dc_notch: bool | None = None,
        mains_notch: int | str | None = None,
        bandpass: bool | None = None,
        flo: int | None = None,
        fhi: int | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the ECG sensor. See sifibridge `help configure ecg`.

        Every parameter is optional and defaults to None, meaning "leave this
        setting as it is on the device". `configure_ecg(fs=1000)` changes only
        the sampling rate and preserves the filters you configured earlier.

        :param fs: Sampling rate in Hz. Possible values: 250, 500, 1000, 2000.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch frequency in Hz: 50 or 60. Pass
            ``"off"`` (or ``0``/``False``) to disable the filter, None to leave
            it unchanged.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        """
        return self._configure(
            self._build_sensor_filter_cmd(
                "ecg", fs, dc_notch, mains_notch, bandpass, flo, fhi
            ),
            all,
            devices,
        )

    def configure_emg(
        self,
        fs: int | None = None,
        dc_notch: bool | None = None,
        mains_notch: int | str | None = None,
        bandpass: bool | None = None,
        flo: int | None = None,
        fhi: int | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the EMG sensor. See sifibridge `help configure emg`.

        Every parameter is optional and defaults to None, meaning "leave this
        setting as it is on the device".

        :param fs: Sampling rate in Hz. Possible values: 500, 1000, 1600
            (SiFiBand only), 2000.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch frequency in Hz: 50 or 60. Pass
            ``"off"`` (or ``0``/``False``) to disable the filter, None to leave
            it unchanged.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        """
        return self._configure(
            self._build_sensor_filter_cmd(
                "emg", fs, dc_notch, mains_notch, bandpass, flo, fhi
            ),
            all,
            devices,
        )

    def configure_eda(
        self,
        fs: int | None = None,
        dc_notch: bool | None = None,
        mains_notch: int | str | None = None,
        bandpass: bool | None = None,
        flo: int | None = None,
        fhi: int | None = None,
        freq: int | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the EDA/BIOZ sensor.

        Every parameter is optional and defaults to None, meaning "leave this
        setting as it is on the device".

        **Warning**: Enabling BIOZ and ECG/EMG at the same time may cause
        interference and degrade ECG/EMG quality.

        :param fs: Sampling rate in Hz. Possible values: 4, 8, 16, 32, 50.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch frequency in Hz: 50 or 60. Pass
            ``"off"`` (or ``0``/``False``) to disable the filter, None to leave
            it unchanged.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        :param freq: EDA/BIOZ excitation signal frequency (Hz). 0 for a DC
            measurement.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        """
        tail = self._join(
            self._build_sensor_filter_cmd(
                "eda", fs, dc_notch, mains_notch, bandpass, flo, fhi
            ),
            self._flag("freq", freq),
        )
        return self._configure(tail, all, devices)

    def _check_ppg_rate(
        self,
        sps: int | None,
        avg: int | None,
        all: bool,
        devices: str | Sequence[str] | None,
    ) -> None:
        """Enforce the wrapper's cap on the PPG effective rate (``sps / avg``).

        Either value may be omitted, in which case the device's current setting
        stands — so the missing one is read back from `get_configuration` to
        work out what the rate will actually be. That read is only possible for
        the active device, so when the call targets a different or wider set,
        the check is skipped with a warning rather than silently applied to the
        wrong device's numbers.

        :raises ValueError: If the resulting rate exceeds the cap.
        """
        if sps is None and avg is None:
            return  # nothing about the rate is changing

        if all or devices:
            logger.warning(
                "Cannot check the PPG rate cap for a multi-device configure: "
                "the current sps/avg would have to be read per device. Pass "
                "both sps and avg to have it checked."
            )
            if sps is None or avg is None:
                return

        if sps is None or avg is None:
            current = self.get_configuration().get("ppg", {})
            sps = current.get("sps") if sps is None else sps
            avg = current.get("avg") if avg is None else avg
            if sps is None or not avg:
                logger.warning(
                    "Device did not report its current PPG sps/avg; skipping "
                    "the effective-rate check"
                )
                return

        effective = sps / avg
        if effective > self._PPG_MAX_EFFECTIVE_RATE_HZ:
            raise ValueError(
                f"Effective PPG output rate sps/avg = {sps}/{avg} = "
                f"{effective:g} Hz exceeds the "
                f"{self._PPG_MAX_EFFECTIVE_RATE_HZ:g} Hz cap; raise avg or "
                "lower sps"
            )

    def configure_ppg(
        self,
        sps: int | None = None,
        ir: int | None = None,
        red: int | None = None,
        green: int | None = None,
        blue: int | None = None,
        sens: PpgSensitivity | str | None = None,
        avg: int | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the PPG sensor. See sifibridge `help configure ppg`.

        Every parameter is optional and defaults to None, meaning "leave this
        setting as it is on the device".

        :param sps: Raw AFE sample rate in Hz. Possible values: 50, 100, 200,
            400, 800.
        :param ir: IR LED current in mA (0-50).
        :param red: Red LED current in mA (0-50).
        :param green: Green LED current in mA (0-50).
        :param blue: Blue LED current in mA (0-50).
        :param sens: Sensor sensitivity. One of low, medium, high, max.
        :param avg: Signal averaging factor. Higher values give smoother
            signals but a slower response to change. Possible values: 1, 2, 4,
            8, 16, 32.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.

        The effective output rate is ``sps / avg`` — for example ``sps=400``
        with ``avg=4`` yields 100 Hz — and is reported live in each packet's
        ``sample_rate``. The wrapper caps it at 200 Hz.

        Setting only one of the two is fine: the other is read back from the
        device so the cap can still be checked.

        :raises ValueError: If `avg` is not positive, or if the resulting
            ``sps / avg`` exceeds 200 Hz.
        """
        if avg is not None and avg <= 0:
            raise ValueError(f"avg must be positive, got {avg}")
        self._check_ppg_rate(sps, avg, all, devices)
        if isinstance(sens, str):
            sens = PpgSensitivity(sens)
        tail = self._join(
            "ppg",
            self._flag("sps", sps),
            self._flag("led-ir", ir),
            self._flag("led-red", red),
            self._flag("led-green", green),
            self._flag("led-blue", blue),
            self._flag("sens", sens),
            self._flag("avg", avg),
        )
        return self._configure(tail, all, devices)

    def configure_ppg_fs(
        self,
        fs: int,
        avg: int | None = None,
        ir: int | None = None,
        red: int | None = None,
        green: int | None = None,
        blue: int | None = None,
        sens: PpgSensitivity | str | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure PPG by the output rate you want, rather than by `sps`.

        The PPG front end has no sampling-rate control as such: it samples at
        `sps` and averages `avg` of those into each delivered sample, so the
        rate that reaches you is ``sps / avg``. This works out a pair that
        delivers `fs` and hands it to `configure_ppg`.

        By default it picks the pair with the **most averaging** — the highest
        `sps` that still divides down to `fs` — since averaging is the reason
        the two are separate: more raw samples per delivered one means a
        cleaner signal, at the cost of running the LEDs harder. Pass `avg`
        explicitly to choose the trade-off yourself.

        :param fs: Wanted output rate in Hz, up to 200.
        :param avg: Averaging factor to use. Defaults to the largest that can
            deliver `fs`.
        :param ir: IR LED current in mA (0-50).
        :param red: Red LED current in mA (0-50).
        :param green: Green LED current in mA (0-50).
        :param blue: Blue LED current in mA (0-50).
        :param sens: Sensor sensitivity. One of low, medium, high, max.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.

        :raises ValueError: If no ``sps``/``avg`` pair delivers `fs`, or if
            `fs` exceeds the 200 Hz cap. The message lists the rates that are
            reachable.

        # Example

        ```python
        >>> sb.configure_ppg_fs(100)           # sps=800, avg=8
        >>> sb.configure_ppg_fs(100, avg=1)    # sps=100, avg=1
        ```
        """
        sps, avg = self._solve_ppg_rate(fs, avg)
        return self.configure_ppg(
            sps=sps,
            ir=ir,
            red=red,
            green=green,
            blue=blue,
            sens=sens,
            avg=avg,
            all=all,
            devices=devices,
        )

    @classmethod
    def _solve_ppg_rate(cls, fs: int, avg: int | None) -> tuple:
        """Find an ``(sps, avg)`` pair whose quotient is `fs`.

        Prefers the largest `avg` — equivalently the highest `sps` — because
        that is the smoothest way to deliver a given rate. See
        `configure_ppg_fs`.
        """
        if fs <= 0:
            raise ValueError(f"fs must be positive, got {fs}")
        if fs > cls._PPG_MAX_EFFECTIVE_RATE_HZ:
            raise ValueError(
                f"fs={fs} Hz exceeds the {cls._PPG_MAX_EFFECTIVE_RATE_HZ:g} Hz "
                f"cap; reachable rates are {cls._reachable_ppg_rates()}"
            )

        candidates = [
            (sps, a)
            for a in cls._PPG_AVG_CHOICES
            for sps in cls._PPG_SPS_CHOICES
            if sps == fs * a
        ]
        if avg is not None:
            candidates = [pair for pair in candidates if pair[1] == avg]
            if not candidates:
                raise ValueError(
                    f"No PPG configuration delivers fs={fs} Hz with avg={avg}: "
                    f"that needs sps={fs * avg}, and the front end offers "
                    f"{list(cls._PPG_SPS_CHOICES)}"
                )
        if not candidates:
            raise ValueError(
                f"No PPG configuration delivers fs={fs} Hz; reachable rates "
                f"are {cls._reachable_ppg_rates()}"
            )
        # Highest sps, which is also the largest avg for this fs.
        return max(candidates)

    @classmethod
    def _reachable_ppg_rates(cls) -> list:
        """Every output rate the front end can deliver, within the cap."""
        return sorted(
            {
                sps // avg
                for sps in cls._PPG_SPS_CHOICES
                for avg in cls._PPG_AVG_CHOICES
                if sps % avg == 0 and sps / avg <= cls._PPG_MAX_EFFECTIVE_RATE_HZ
            }
        )

    def configure_imu(
        self,
        fs: int | None = None,
        accel_range: int | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the IMU. See sifibridge `help configure imu`.

        Every parameter is optional and defaults to None, meaning "leave this
        setting as it is on the device".

        :param fs: Sampling rate in Hz. Possible values: 25, 50, 100, 200.
        :param accel_range: Accelerometer range in g. Possible values: 8, 16.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :raises ValueError: If `accel_range` is not 8 or 16.

        **NOTE**: sifibridge 2.0.0 removed the gyroscope range setting. The
        firmware reads the IMU through its 20-bit high-resolution FIFO, which
        pins the gyroscope full scale per IMU part, so the setting never
        changed the delivered range. The IMU part is reported as ``chip`` in
        `info()`, and the pinned full scale for each part is in the device
        spec sheet.
        """
        if accel_range is not None and accel_range not in (8, 16):
            raise ValueError(
                f"accel_range must be 8 or 16 (got {accel_range}). sifibridge "
                "2.0.0 dropped the narrower ranges: they buy no resolution "
                "from the high-resolution FIFO and only clip"
            )
        tail = self._join(
            "imu",
            self._flag("fs", fs),
            self._flag("acc-range", accel_range),
        )
        return self._configure(tail, all, devices)

    def configure_temperature(
        self,
        fs: float | None = None,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Configure the skin temperature sensor.

        :param fs: Sampling rate in Hz. Possible values: 0.1, 1, 2, 10. None
            leaves it unchanged.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.

        **NOTE**: temperature has no enable of its own — it is not part of
        `configure_sensors` — but the device only emits it alongside an
        otherwise active acquisition, so at least one other sensor must be
        enabled for temperature packets to arrive.
        """
        # The binary's allowed values are "0.1", "1", "2", "10"; a Python float
        # renders 1.0 as "1.0", which clap rejects. ``_flag`` formats floats
        # with ``:g``, which drops the trailing ".0".
        return self._configure(
            self._join("temperature", self._flag("fs", fs)), all, devices
        )

    def set_onboard_filtering(
        self,
        enable: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Enable or disable the device's onboard filtering for all sensors."""
        state = "on" if enable else "off"
        return self._configure(f"filtering {state}", all, devices)

    def set_high_gain(
        self,
        enable: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Enable or disable high gain on the ECG and EMG ADC.

        High gain uses more of the dynamic range but saturates the sensor more
        easily. Disabling reverts to normal gain.
        """
        state = "on" if enable else "off"
        return self._configure(f"high-gain {state}", all, devices)

    def set_memory_mode(
        self,
        memory_config: MemoryMode | str,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Configure the device's memory mode.

        **NOTE**: See `MemoryMode` for more information.
        """
        if isinstance(memory_config, str):
            memory_config = MemoryMode(memory_config)
        return self._configure(f"memory {memory_config.value}", all, devices)

    def set_low_latency_mode(
        self,
        on: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Set the low latency data mode.

        **NOTE**: Only supported on select BioPoint versions. Ask SiFi Labs directly.
        """
        state = "on" if on else "off"
        return self._configure(f"low-latency {state}", all, devices)

    def set_ble_power(
        self,
        power: BleTxPower | str,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Set the BLE transmission power."""
        if isinstance(power, str):
            power = BleTxPower(power)
        return self._configure(f"ble-power {power.value}", all, devices)

    def set_night_mode(
        self,
        enable: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Enable/disable night mode (LEDs off during acquisition)."""
        state = "on" if enable else "off"
        return self._configure(f"night {state}", all, devices)

    def set_motor_intensity(
        self,
        level: int,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Set the vibration motor intensity level (1-10).

        :param level: Intensity, 1 to 10.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :raises ValueError: If level is not between 1 and 10.
        """
        if not 1 <= level <= 10:
            raise ValueError(
                f"Motor intensity level must be between 1 and 10, got {level}"
            )
        return self._targeted(f"motor --intensity {level}", "motor", all, devices)

    @classmethod
    def _build_sensor_filter_cmd(
        cls,
        sensor: str,
        fs: int | None,
        dc_notch: bool | None,
        mains_notch: int | str | None,
        bandpass: bool | None,
        flo: int | None,
        fhi: int | None,
    ) -> str:
        """Build the ``configure <sensor> ...`` tail, omitting unset parameters.

        Omission is meaningful: sifibridge 2.0.0 leaves a setting untouched
        when its flag is absent, so a None parameter must emit nothing.
        """
        return cls._join(
            sensor,
            cls._flag("fs", fs),
            cls._flag("dc-notch", dc_notch),
            cls._mains_notch_flag(mains_notch),
            cls._flag("bandpass", bandpass),
            cls._flag("bandpass-low", flo),
            cls._flag("bandpass-high", fhi),
        )

    # ------------------------------------------------------------------
    # Device actions
    # ------------------------------------------------------------------

    def download_memory_ble(
        self, output_dir: str, fmt: str = "csv", timeout: float = 3600 * 12
    ) -> dict:
        """
        Download the active device's onboard memory over BLE and export it to
        file.

        In sifibridge 2.0.0 `download-memory` loads the recordings into the
        buffering subsystem rather than writing files, so this blocks until the
        download completes and then runs `buffer_export()` for you.

        :param output_dir: Directory to save the exported data. Must not
            contain whitespace (see `buffer_export`).
        :param fmt: Output format. Either ``"csv"`` or ``"hdf5"``.
        :param timeout: How long to wait for the download. Downloading a full
            flash over BLE takes hours, hence the very long default.

        :return: The ``buffer_export`` response payload.
        :raises SifiBridgeTimeout: If the download does not finish in time.
        """
        self._request("download-memory", timeout=timeout)
        active_device = self.get_active_device()
        return self.buffer_export(fmt=fmt, output_dir=output_dir, device=active_device)

    def download_memory_serial(
        self,
        port: str,
        output_dir: str,
        fmt: str = "csv",
        timeout: float = 3600 * 4,
    ) -> dict:
        """
        Download the active device's onboard memory over serial and export it
        to file. Blocking, like `download_memory_ble`.

        :param port: Serial port to use (e.g. ``COM3``, ``/dev/ttyACM0``).
        :param output_dir: Directory to save the exported data. Must not
            contain whitespace (see `buffer_export`).
        :param fmt: Output format. Either ``"csv"`` or ``"hdf5"``.
        :param timeout: How long to wait for the download.

        :return: The ``buffer_export`` response payload.
        """
        self._check_repl_token(port, "Serial port")
        self._request(f"download-memory --serial {port}", timeout=timeout)
        active_device = self.get_active_device()
        return self.buffer_export(fmt=fmt, output_dir=output_dir, device=active_device)

    def erase_onboard_memory(
        self,
        format: bool = False,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Erase the device's onboard flash.

        :param format: Fully format the flash instead of only erasing the
            stored recordings. Takes longer, but leaves the memory completely
            clear.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.

        :return: The ``erase_memory`` payload, or a list of per-device
            responses when targeting several devices.
        :raises SifiBridgeError: If sifibridge returns an error response.
        """
        cmd = self._join("erase-memory", "--format" if format else "")
        return self._targeted(cmd, "erase_memory", all, devices)

    def power_off(
        self,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """Power off the device.

        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :return: The ``power_off`` payload, or a list of per-device responses
            when targeting several devices.
        """
        return self._targeted("power-off", "power_off", all, devices)

    def dfu(
        self,
        package: str,
        handle: str | None = None,
        resume: bool = False,
        timeout: float = 1800.0,
    ) -> dict:
        """
        Update a device's firmware over BLE.

        :param package: Path to the DFU zip package. Must not contain
            whitespace: the sifibridge REPL splits on whitespace and does not
            support quoting, so a path like ``C:/My Files/pkg.zip`` cannot be
            expressed. Move the package somewhere without spaces first.
        :param handle: Device handle to update. Defaults to the active device.
        :param resume: Resume a DFU that was interrupted.
        :param timeout: How long to wait for the update to finish.

        :return: The ``dfu`` response payload.
        :raises ValueError: If `package` or `handle` contains whitespace.
        :raises SifiBridgeError: If sifibridge returns an error response.
        """
        self._check_repl_token(package, "DFU package path")
        if handle is not None:
            self._check_repl_token(handle, "Device handle")
        cmd = self._join(
            "dfu",
            self._flag("handle", handle),
            "--resume" if resume else "",
            package,
        )
        return self._request(cmd, timeout=timeout)["dfu"]

    def set_led(
        self,
        index: int,
        on: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Turn LED `index` on or off.

        :param index: LED to set, either `1` or `2`.
        :param on: True to turn on, False to turn off.
        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.

        :raises ValueError: If `index` is not 1 or 2.
        :return: The ``led`` payload, or a list of per-device responses when
            targeting several devices.
        """
        if index not in (1, 2):
            raise ValueError(f"LED index must be 1 or 2, got {index}")
        state = "on" if on else "off"
        return self._targeted(f"led --state {state} {index}", "led", all, devices)

    def set_motor(
        self,
        on: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Start or stop the vibration motor.

        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :return: The ``motor`` payload, or a list of per-device responses when
            targeting several devices.
        """
        state = "on" if on else "off"
        return self._targeted(f"motor --state {state}", "motor", all, devices)

    def set_status_updates(
        self,
        on: bool,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Enable or stop status updates.

        Status updates are periodic (~1s) data packets containing information
        such as memory used, memory size, etc.

        :param all: Apply to every managed device.
        :param devices: Device handle, or sequence of handles, to apply to.
        :return: The ``status_update`` payload, or a list of per-device
            responses when targeting several devices.
        """
        state = "on" if on else "off"
        return self._targeted(f"status-update {state}", "status_update", all, devices)

    def start(
        self,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
        set_default: bool = False,
    ):
        """
        Start an acquisition.

        :param all: Start on every managed device.
        :param devices: Device handle, or sequence of handles, to start.
        :param set_default: Store the current configuration as the device's
            default, so it is reapplied on the next power-up.

        :return: True on success for a single device. When targeting several
            devices (`all` or `devices`), sifibridge answers with an aggregated
            response and this returns the list of per-device responses.
        """
        cmd = self._join("start", "--set-default" if set_default else "")
        result = self._targeted(cmd, "start", all, devices)
        return result if isinstance(result, list) else result["connected"]

    def stop(
        self,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Stop an acquisition.

        :param all: Stop on every managed device.
        :param devices: Device handle, or sequence of handles, to stop.

        :return: True on success for a single device, else the list of
            per-device responses. See `start`.
        """
        result = self._targeted("stop", "stop", all, devices)
        return result if isinstance(result, list) else result["connected"]

    def send_event(
        self,
        all: bool = False,
        devices: str | Sequence[str] | None = None,
    ):
        """
        Generate a software event, which appears in the data stream as an
        `event` packet timestamped on the device.

        :param all: Send on every managed device.
        :param devices: Device handle, or sequence of handles, to send on.

        :return: True on success for a single device, else the list of
            per-device responses. See `start`.
        """
        result = self._targeted("event", "event", all, devices)
        return result if isinstance(result, list) else result["connected"]

    def buffer_export(
        self,
        fmt: str = "csv",
        output_dir: str = ".",
        device: str | None = None,
    ) -> dict:
        """
        Export a device's buffered acquisitions to file.

        :param fmt: Output format. Either `csv` or `hdf5`.
        :param output_dir: Directory to save the exported data. Must not
            contain whitespace: the sifibridge REPL splits commands on
            whitespace and does not support quoting, so a path like
            ``C:/Users/me/My Data`` cannot be expressed. Export somewhere
            without spaces and move the files afterwards.
        :param device: Device ID or name. Defaults to the active device.

        :raises ValueError: If `output_dir` or `device` contains whitespace
            or ``;``.
        """
        self._check_repl_token(str(output_dir), "Output directory")
        cmd_parts = ["buffer export"]
        if device is not None:
            self._check_repl_token(device, "Device handle")
            cmd_parts.append(f"--handle {device}")
        cmd_parts.append(f"--dir {output_dir}")
        cmd_parts.append(f"--format {fmt}")
        return self._request(" ".join(cmd_parts))["buffer_export"]

    def buffer_list(self, device: str | None = None) -> list[dict]:
        """
        List buffered acquisitions and their status.

        :param device: Device ID or name to filter by. Defaults to all devices.
        :return: A list of acquisition info dicts, each with keys ``id``,
            ``device``, ``start_time``, ``sensors`` (a list of
            ``{"name", "num_samples"}``) and ``total_samples``.

            sifibridge 2.0.0 removed the ``completed`` field: it only flipped
            once the *next* acquisition started, so a stopped acquisition was
            indistinguishable from a running one.
        """
        cmd_parts = ["buffer list"]
        if device is not None:
            self._check_repl_token(device, "Device handle")
            cmd_parts.append(f"--handle {device}")
        return self._request(" ".join(cmd_parts))["buffer_list"]["acquisitions"]

    def buffer_info(
        self,
        device: str | None = None,
        acquisition_id: int | None = None,
    ) -> dict:
        """
        Get detailed info about a single buffered acquisition, including the
        device configuration it was recorded with.

        :param device: Device ID or name. Defaults to the active device.
        :param acquisition_id: Acquisition ID. Defaults to the latest acquisition.
        :return: The ``buffer_info`` payload, with keys ``acquisition`` and
            ``device_config``.
        """
        cmd_parts = ["buffer info"]
        if device is not None:
            self._check_repl_token(device, "Device handle")
            cmd_parts.append(f"--handle {device}")
        if acquisition_id is not None:
            cmd_parts.append(f"--id {acquisition_id}")
        return self._request(" ".join(cmd_parts))["buffer_info"]

    def buffer_pull(
        self,
        sensor: str | list[str],
        device: str | None = None,
        acquisition_id: int | None = None,
        last_seconds: float | None = None,
        from_: float | None = None,
        to: float | None = None,
    ) -> list[dict]:
        """
        Pull buffered sensor data out of an acquisition.

        :param sensor: Sensor type, or a list of them, to pull. Each one of:
            ``ecg``, ``emg``, ``emg_armband``, ``eda``, ``imu``, ``ppg``,
            ``temperature``, ``event``.
        :param device: Device ID or name. Defaults to the active device.
        :param acquisition_id: Acquisition ID. Defaults to the latest acquisition.
        :param last_seconds: If set, only pull the last N seconds of data.
            Mutually exclusive with ``from_``/``to``.
        :param from_: Start of a relative time range, in seconds since recording
            start. Requires ``to``.
        :param to: End of a relative time range, in seconds since recording
            start. Requires ``from_``.

        :raises ValueError: If the time-range arguments are combined illegally.
        :return: A list of per-sensor dicts, each with keys ``sensor``,
            ``timestamps`` and ``values`` (channel name -> samples).
        """
        if last_seconds is not None and (from_ is not None or to is not None):
            raise ValueError("last_seconds is mutually exclusive with from_/to")
        if (from_ is None) != (to is None):
            raise ValueError("from_ and to must be provided together")

        sensors = [sensor] if isinstance(sensor, str) else list(sensor)
        if not sensors:
            raise ValueError("At least one sensor must be provided")

        cmd_parts = ["buffer pull"]
        if device is not None:
            self._check_repl_token(device, "Device handle")
            cmd_parts.append(f"--handle {device}")
        for s in sensors:
            cmd_parts.append(f"--sensor {s}")
        if acquisition_id is not None:
            cmd_parts.append(f"--id {acquisition_id}")
        if last_seconds is not None:
            cmd_parts.append(f"--last-seconds {last_seconds}")
        if from_ is not None:
            cmd_parts.append(f"--from {from_} --to {to}")
        return self._request(" ".join(cmd_parts))["buffer_pull"]["sensors"]

    def buffer_clear(
        self,
        device: str | None = None,
        acquisition_id: int | None = None,
        all: bool = False,
    ) -> dict:
        """
        Clear buffered acquisitions.

        Pass exactly one of `device`, `acquisition_id`, or `all`.

        :param device: Device handle whose acquisitions should all be cleared.
        :param acquisition_id: A single acquisition ID to clear.
        :param all: Clear all buffered data across every device.

        :raises ValueError: If more than one selector is provided.
        :return: The ``buffer_clear`` payload, with a ``message`` key.
        """
        selectors = sum((device is not None, acquisition_id is not None, bool(all)))
        if selectors > 1:
            raise ValueError("Pass at most one of device, acquisition_id, or all")
        cmd_parts = ["buffer clear"]
        if device is not None:
            self._check_repl_token(device, "Device handle")
            cmd_parts.append(f"--handle {device}")
        if acquisition_id is not None:
            cmd_parts.append(f"--id {acquisition_id}")
        if all:
            cmd_parts.append("--all")
        return self._request(" ".join(cmd_parts))["buffer_clear"]

    # ------------------------------------------------------------------
    # Core IO: one generic request/response, two workers, one data queue
    # ------------------------------------------------------------------

    @staticmethod
    def _expected_key(line: str) -> str | None:
        """The top-level response key sifibridge answers `line` with.

        Responses are keyed by the command name in snake_case
        (``status-update`` -> ``status_update``, ``buffer list`` ->
        ``buffer_list``), which lets `_request` tell its own reply apart from
        an unsolicited line. Returns None for an empty command.

        Verified against sifibridge 2.0.0 with a BioPoint on 2026-09-13 for
        every command the wrapper sends except ``dfu``, whose success response
        needs a real firmware package to observe. A wrong key here is not
        fatal: `_request` falls back to the first response it saw.
        """
        parts = line.split()
        if not parts:
            return None
        if parts[0] == "buffer" and len(parts) > 1:
            return f"buffer_{parts[1].replace('-', '_')}"
        return parts[0].replace("-", "_")

    def _request(self, line: str, timeout: float | None = None) -> dict:
        """
        Write one REPL line and read its JSON response.

        Responses are matched to the command by their top-level key rather than
        taken blindly from the head of the queue. A single unsolicited line on
        stdout would otherwise offset every subsequent command by one, for the
        life of the process. Non-matching responses are logged and skipped; if
        nothing matches before the timeout, the first one seen is returned
        anyway (with a warning) so an unanticipated response key degrades to
        the old behaviour instead of hanging.

        :param line: REPL command (no trailing newline).
        :param timeout: Seconds to wait. Defaults to `_DEFAULT_REQUEST_TIMEOUT`.
        :return: Parsed response dict.
        :raises SifiBridgeTimeout: If sifibridge does not respond in time.
        :raises SifiBridgeError: On a `{"error": ...}` response from sifibridge.
        """
        if timeout is None:
            timeout = self._DEFAULT_REQUEST_TIMEOUT
        expected = self._expected_key(line)

        logger.debug(f"-> {line}")
        with self._response_lock:
            assert self._bridge.stdin is not None
            self._bridge.stdin.write(f"{line}\n".encode())
            self._bridge.stdin.flush()
            resp = self._await_response(line, expected, timeout)
        logger.debug(f"<- {resp}")
        if "error" in resp:
            raise SifiBridgeError(
                resp["error"].get("message", "Unknown sifibridge error")
            )
        return resp

    def _await_response(
        self, line: str, expected: str | None, timeout: float
    ) -> dict:
        """Pull responses until one belongs to `line`. See `_request`."""
        deadline = time.monotonic() + timeout
        fallback = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                resp = self._response_queue.get(timeout=remaining)
            except queue.Empty:
                break
            # `error` is how any command fails, and `multi` is how a command
            # targeting several devices succeeds; both belong to this request.
            if (
                expected is None
                or expected in resp
                or "error" in resp
                or "multi" in resp
            ):
                return resp
            if fallback is None:
                fallback = resp
            logger.warning(
                f"Response {list(resp)} does not match command {line!r} "
                f"(expected key {expected!r}); skipping"
            )
        if fallback is not None:
            logger.warning(
                f"No response keyed {expected!r} for {line!r}; returning the "
                f"first response received ({list(fallback)})"
            )
            return fallback
        raise SifiBridgeTimeout(f"No response to {line!r} within {timeout}s")

    def _response_worker(self):
        """Read JSON lines from sifibridge's stdout into `_response_queue`."""
        assert self._bridge.stdout is not None
        try:
            for raw in iter(self._bridge.stdout.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                try:
                    self._response_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"Non-JSON response line ignored: {raw!r}")
        except Exception as e:
            logger.error(f"Response worker stopped: {e}")

    def _data_worker(self):
        """Read newline-delimited JSON sensor packets from the TCP socket.

        Each packet is pushed to the generic `_data_queue` (for `get_data()`)
        and, if its `packet_type` maps to a sensor, also pushed to the matching
        typed queue (for `get_ecg()`, `get_emg()`, etc.).
        """
        buf = b""
        try:
            while True:
                chunk = self._data_sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        packet = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON data line ignored: {line!r}")
                        continue
                    self._data_queue.put(packet)
                    sensor = self._PACKET_TYPE_TO_SENSOR.get(packet.get("packet_type"))
                    if sensor is not None:
                        self._typed_queues[sensor].put(packet)
        except OSError as e:
            # close() closes the socket out from under a blocked recv(), which
            # surfaces here as EBADF; that is expected teardown, not an error.
            if self._closed:
                logger.debug(f"Data worker stopped during shutdown: {e}")
            else:
                logger.info(f"Data worker stopped: {e}")

    def clear_data_buffer(self) -> int:
        """
        Drain all internal sensor-data queues (generic + per-sensor).

        :return: Number of packets discarded from the generic queue. Each
            packet is queued in both the generic queue and (when applicable)
            its sensor queue, so this is the true per-packet count rather than
            the sum across queues.
        """
        count = 0
        try:
            while True:
                self._data_queue.get_nowait()
                count += 1
        except queue.Empty:
            pass
        for q in self._typed_queues.values():
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        return count

    def get_data(self, timeout: float | None = None) -> dict:
        """
        Pop the next sensor-data packet of any type.

        :param timeout: Seconds to wait. None to block indefinitely.
        :return: Packet dict, or `{}` if the timeout elapses.

        **NOTE**: Do not mix `get_data()` with the per-sensor getters
        (`get_ecg()`, `get_emg()`, etc.) on the same `SifiBridge` instance.
        Each packet is queued in both the generic queue and its sensor queue,
        so interleaving the two APIs will return duplicates.
        """
        try:
            return self._data_queue.get(timeout=timeout)
        except queue.Empty:
            return {}

    def _get_sensor_packet(self, sensor: str, timeout: float | None) -> dict:
        """Pop the next packet from the named sensor queue. `{}` on timeout."""
        try:
            return self._typed_queues[sensor].get(timeout=timeout)
        except queue.Empty:
            return {}

    def get_ecg(self, timeout: float | None = None) -> dict:
        """Pop the next ECG packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("ecg", timeout)

    def get_emg(self, timeout: float | None = None) -> dict:
        """
        Pop the next EMG packet (BioPoint `emg` or SiFiBand `emg_armband`).
        Returns `{}` if `timeout` elapses.
        """
        return self._get_sensor_packet("emg", timeout)

    def get_eda(self, timeout: float | None = None) -> dict:
        """Pop the next EDA packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("eda", timeout)

    def get_imu(self, timeout: float | None = None) -> dict:
        """Pop the next IMU packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("imu", timeout)

    def get_ppg(self, timeout: float | None = None) -> dict:
        """Pop the next PPG packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("ppg", timeout)

    def get_temperature(self, timeout: float | None = None) -> dict:
        """Pop the next temperature packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("temperature", timeout)

    def get_event(self, timeout: float | None = None) -> dict:
        """Pop the next event packet. Returns `{}` if `timeout` elapses."""
        return self._get_sensor_packet("event", timeout)

    def close(self) -> None:
        """
        Shut down the sifibridge subprocess and release the data socket.

        Sends a `quit` REPL command, closes stdin, closes the data socket,
        and waits up to 2s for the subprocess to exit cleanly before
        killing it. Safe to call multiple times.

        Prefer using `SifiBridge` as a context manager
        (`with SifiBridge() as sb:`) so this runs automatically.
        """
        if self._closed:
            return
        self._closed = True

        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            try:
                if bridge.stdin and not bridge.stdin.closed:
                    bridge.stdin.write(b"quit\n")
                    bridge.stdin.flush()
                    bridge.stdin.close()
            except (OSError, ValueError):
                pass

        sock = getattr(self, "_data_sock", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        if bridge is not None:
            try:
                bridge.wait(timeout=2)
            except sp.TimeoutExpired:
                bridge.kill()
                try:
                    bridge.wait(timeout=1)
                except sp.TimeoutExpired:
                    pass
            if bridge.stdout is not None and not bridge.stdout.closed:
                try:
                    bridge.stdout.close()
                except OSError:
                    pass

    def __enter__(self) -> "SifiBridge":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
