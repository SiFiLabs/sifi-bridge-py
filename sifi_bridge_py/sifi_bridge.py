import subprocess as sp
import json
import socket
import time
from enum import Enum
import threading
import queue

import logging

logger = logging.getLogger(__name__)


class PacketType(Enum):
    """
    Data packet types that can be received from SiFi Bridge.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_ecg()
    >>> print(packet["packet_type"] == PacketType.ECG.value)
    True
    ```
    """

    ECG = "ecg"
    EMG = "emg"
    EMG_ARMBAND = "emg_armband"
    EDA = "eda"
    IMU = "imu"
    PPG = "ppg"
    TEMPERATURE = "temperature"
    MEMORY = "memory"
    STATUS = "status"
    START_TIME = "start_time"
    EVENT = "event"
    INVALID = "invalid"


class PacketStatus(Enum):
    """
    Data packet statuses.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_ecg()
    >>> print(packet["status"] == PacketStatus.OK.value)
    True
    ```
    """

    OK = "ok"
    RECORDING = "recording"
    MEMORY_DOWNLOAD_COMPLETED = "memory_download_completed"
    MEMORY_ERASED = "memory_erased"
    INVALID_DATETIME = "invalid_datetime"
    INVALID = "invalid"


class SensorChannel(Enum):
    """
    Sensor channel names as returned by `sifibridge`.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_imu()
    >>> imu = packet["data"]
    >>> print(len(imu) == len(SensorChannel.IMU.value)) # 7 IMU channels
    True
    >>> qw = imu[SensorChannel.IMU.value[0]] # get first channel
    >>> print(len(qw), qw)
    8 [0.5427, 0.5423, 0.5426, 0.5424, 0.5424, 0.5428, 0.5424, 0.5422]
    ```
    """

    ECG = "ecg"
    """ECG sensor channel."""
    EMG = "emg"
    """EMG sensor channel."""
    EMG_ARMBAND = ("emg0", "emg1", "emg2", "emg3", "emg4", "emg5", "emg6", "emg7")
    """SiFiBand 8-channel EMG sensor channels."""
    EDA = "eda"
    """EDA/BIOZ sensor channel."""
    IMU = ("qw", "qx", "qy", "qz", "ax", "ay", "az")
    """IMU sensor channels."""
    PPG = ("ir", "r", "g", "b")
    """PPG sensor channels."""
    TEMPERATURE = "temperature"
    """Temperature sensor channel."""
    EVENT = "event"
    """Event data key"""


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


class DeviceType(Enum):
    """
    DEPRECATED: This enum is considered legacy since the custom naming functionality is supported.

    Use in tandem with SifiBridge.connect() to connect to SiFi Devices via BLE name.
    """

    BIOPOINT_V1_1 = "BioPoint_v1_1"
    BIOPOINT_V1_2 = "BioPoint_v1_2"
    BIOPOINT_V1_3 = "BioPoint_v1_3"
    SIFIBAND = "SiFiBand"


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

    def select_device(self, name: str) -> str:
        """
        Select a device by ID or BLE local name.

        :raises ValueError: if `name` contains spaces.
        :raises SifiBridgeError: If no device matches `name`.

        :return: Active device ID
        """
        if " " in name:
            raise ValueError(f"Spaces are not supported in device name ({name})")
        self._request(f"select {name}")
        return self.get_active_device()

    def rename_device(self, name: str | None = None) -> dict:
        """
        Rename the active device. Pass `None` to reset the device's name to its default value.

        :raises ValueError: if `name` contains spaces.
        :raises SifiBridgeError: If no device is currently connected.

        :return: `rename` response payload.
        """
        if name is not None and " " in name:
            raise ValueError(f"Spaces are not supported in device name ({name})")
        cmd = "rename --reset" if name is None else f"rename {name}"
        return self._request(cmd)["rename"]

    def list_devices(self, source: ListSources | str) -> list[str]:
        """
        List all devices found from a given `source`.
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
        :raises SifiBridgeError: sifibridge returned an error.
        """
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

    def configure_sensors(
        self,
        ecg: bool = False,
        emg: bool = False,
        eda: bool = False,
        imu: bool = False,
        ppg: bool = False,
    ):
        """Configure the enabled sensors."""
        cmd = (
            "configure sensors"
            f" --ecg {'on' if ecg else 'off'}"
            f" --emg {'on' if emg else 'off'}"
            f" --eda {'on' if eda else 'off'}"
            f" --imu {'on' if imu else 'off'}"
            f" --ppg {'on' if ppg else 'off'}"
        )
        return self._request(cmd)["configure"]

    def configure_ecg(
        self,
        fs: int = 500,
        dc_notch: bool = True,
        mains_notch: None | int = 50,
        bandpass: bool = True,
        flo: int = 0,
        fhi: int = 30,
    ) -> dict:
        """Configure ECG sensor. See sifibridge `help configure ecg` for full semantics.

        :param fs: Sampling rate in Hz. Possible values: 250, 500, 1000, 2000.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch filter frequency in Hz. Possible values:
            50, 60, or None to disable.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        """
        return self._request(
            self._build_sensor_filter_cmd(
                "ecg", fs, dc_notch, mains_notch, bandpass, flo, fhi
            )
        )["configure"]

    def configure_emg(
        self,
        fs: int = 2000,
        dc_notch: bool = True,
        mains_notch: None | int = 50,
        bandpass: bool = True,
        flo: int = 20,
        fhi: int = 450,
    ) -> dict:
        """Configure EMG sensor. See sifibridge `help configure emg`.

        :param fs: Sampling rate in Hz. Possible values: 500, 1000, 1600 (SiFiBand only), 2000.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch filter frequency in Hz. Possible values:
            50, 60, or None to disable.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        """
        return self._request(
            self._build_sensor_filter_cmd(
                "emg", fs, dc_notch, mains_notch, bandpass, flo, fhi
            )
        )["configure"]

    def configure_eda(
        self,
        fs: int = 50,
        dc_notch: bool = True,
        mains_notch: int | None = 50,
        bandpass: bool = True,
        flo: int = 0,
        fhi: int = 5,
        freq: int = 0,
    ) -> dict:
        """
        Configure EDA/BIOZ sensor.

        **Warning**: Enabling BIOZ and ECG/EMG at the same time may cause interference
        and degrade ECG/EMG quality.

        :param fs: Sampling rate in Hz. Possible values: 4, 8, 16, 32, 50.
        :param dc_notch: Enable the DC notch filter.
        :param mains_notch: Mains notch filter frequency in Hz. Possible values:
            50, 60, or None to disable.
        :param bandpass: Enable the bandpass filter.
        :param flo: Bandpass filter lower cutoff frequency in Hz.
        :param fhi: Bandpass filter higher cutoff frequency in Hz.
        :param freq: EDA/BIOZ excitation signal frequency (Hz). 0 for DC measurement.
        """
        cmd = self._build_sensor_filter_cmd(
            "eda", fs, dc_notch, mains_notch, bandpass, flo, fhi
        )
        return self._request(f"{cmd} --freq {freq}")["configure"]

    def configure_ppg(
        self,
        sps: int = 100,
        ir: int = 9,
        red: int = 9,
        green: int = 9,
        blue: int = 9,
        sens: PpgSensitivity | str = PpgSensitivity.MEDIUM,
        avg: int = 1,
    ) -> dict:
        """Configure PPG sensor. See sifibridge `help configure ppg` for full semantics.

        :param sps: Raw AFE sample rate in Hz. The effective output rate delivered
            over the stream is ``sps / avg``, and is reported live in each packet's
            ``sample_rate``. Possible values: 50, 100, 200, 400, 800.
            The effective output rate must be <=400 Hz.
        :param ir: IR LED current in mA (0-50).
        :param red: Red LED current in mA (0-50).
        :param green: Green LED current in mA (0-50).
        :param blue: Blue LED current in mA (0-50).
        :param sens: Sensor sensitivity. Possible values: low, medium, high, max.
        :param avg: Signal averaging factor. Higher values provide smoother signals
            but slower response to changes. Possible values: 1, 2, 4, 8, 16, 32.

        The effective sampling rate is ``sps / avg``. For example, ``sps=400`` with
        ``avg=4`` yields an effective output rate of 100 Hz. Choose ``sps`` (raw AFE
        rate) and ``avg`` (averaging factor) together to trade off the effective rate
        against signal smoothness.
        """
        if isinstance(sens, str):
            sens = PpgSensitivity(sens)
        cmd = (
            "configure ppg"
            f" --sps {sps}"
            f" --led-ir {ir} --led-red {red} --led-green {green} --led-blue {blue}"
            f" --sens {sens.value}"
            f" --avg {avg}"
        )
        return self._request(cmd)["configure"]

    def configure_imu(
        self,
        fs: int = 100,
        accel_range: int = 2,
        gyro_range: int = 16,
    ) -> dict:
        """Configure IMU sensor. See sifibridge `help configure imu`.

        :param fs: Sampling rate in Hz. Possible values: 25, 50, 100, 200.
        :param accel_range: Accelerometer range in g. Possible values: 2, 4, 8, 16.
        :param gyro_range: Gyroscope range in dps. Possible values:
            16, 31, 63, 125, 250, 500, 1000, 2000.
        """
        cmd = (
            "configure imu"
            f" --fs {fs}"
            f" --acc-range {accel_range}"
            f" --gyro-range {gyro_range}"
        )
        return self._request(cmd)["configure"]

    def configure_temperature(self, fs: float = 1.0) -> dict:
        """Configure skin temperature sensor.

        :param fs: Sampling rate in Hz. Possible values: 0.1, 1, 2, 10.
        """
        # The binary's allowed values are "0.1", "1", "2", "10"; a Python float
        # renders 1.0 as "1.0", which clap rejects (and it replies only on
        # stderr, so the request would hang). ``:g`` drops the trailing ".0".
        cmd = f"configure temperature --fs {fs:g}"
        return self._request(cmd)["configure"]

    def set_onboard_filtering(self, enable: bool) -> dict:
        """Enable or disable the device's onboard filtering for all sensors."""
        return self._request(f"configure filtering {'on' if enable else 'off'}")[
            "configure"
        ]

    def set_high_gain(self, enable: bool) -> dict:
        """
        Enable or disable high gain on the ECG and EMG ADC.

        High gain uses more of the dynamic range but saturates the sensor more
        easily. Disabling reverts to normal gain.
        """
        return self._request(f"configure high-gain {'on' if enable else 'off'}")[
            "configure"
        ]

    def set_memory_mode(self, memory_config: MemoryMode | str):
        """
        Configure the device's memory mode.

        **NOTE**: See `MemoryMode` for more information.
        """
        if isinstance(memory_config, str):
            memory_config = MemoryMode(memory_config)
        return self._request(f"configure memory {memory_config.value}")["configure"]

    def set_low_latency_mode(self, on: bool):
        """
        Set the low latency data mode.

        **NOTE**: Only supported on select BioPoint versions. Ask SiFi Labs directly.
        """
        return self._request(f"configure low-latency {'on' if on else 'off'}")[
            "configure"
        ]

    def set_ble_power(self, power: BleTxPower | str):
        """Set the BLE transmission power."""
        if isinstance(power, str):
            power = BleTxPower(power)
        return self._request(f"configure ble-power {power.value}")["configure"]

    def set_night_mode(self, enable: bool):
        """Enable/disable night mode (LEDs off during acquisition)."""
        return self._request(f"configure night {'on' if enable else 'off'}")[
            "configure"
        ]

    def set_motor_intensity(self, level: int):
        """
        Set the vibration motor intensity level (1-10).

        :raises ValueError: If level is not between 1 and 10
        """
        if not 1 <= level <= 10:
            raise ValueError(
                f"Motor intensity level must be between 1 and 10, got {level}"
            )
        return self._request(f"motor --intensity {level}")["motor"]

    @staticmethod
    def _build_sensor_filter_cmd(
        sensor: str,
        fs: int,
        dc_notch: bool,
        mains_notch: int | None,
        bandpass: bool,
        flo: int,
        fhi: int,
    ) -> str:
        if mains_notch == 50:
            mains = "--mains-notch 50"
        elif mains_notch == 60:
            mains = "--mains-notch 60"
        else:
            mains = "--mains-notch off"
        return (
            f"configure {sensor}"
            f" --fs {fs}"
            f" --dc-notch {'on' if dc_notch else 'off'}"
            f" {mains}"
            f" --bandpass {'on' if bandpass else 'off'}"
            f" --bandpass-low {flo}"
            f" --bandpass-high {fhi}"
        )

    def download_memory_ble(self, output_dir: str, fmt: str = "csv") -> dict:
        """
        Download memory over BLE and export it to file. This is a blocking function,
        although sifibridge has an internal timeout of ~5s in case the download fails.

        :param output_dir: Directory to save the exported data.
        :param fmt: Output format. Either ``"csv"`` or ``"hdf5"``.

        :return: The ``buffer_export`` response payload.
        :raises SifiBridgeTimeout: If the completion packet does not arrive
            within ``timeout``.
        """
        self._request("download-memory", timeout=3600 * 12)
        active_device = self.get_active_device()
        return self.buffer_export(fmt=fmt, output_dir=output_dir, device=active_device)

    def download_memory_serial(
        self, port: str, output_dir: str, fmt: str = "csv"
    ) -> dict:
        """
        Download memory over serial and export it to file. This is a blocking function,
        although sifibridge has an internal timeout of ~5s in case the download fails.

        :param port: Serial port to use.
        :param output_dir: Data output directory.
        :param fmt: Output format. Either `csv` or `hdf5`.

        :return: The `buffer_export` response payload.
        """
        self._request(f"download-memory --serial {port}", timeout=3600 * 4)
        active_device = self.get_active_device()
        return self.buffer_export(fmt=fmt, output_dir=output_dir, device=active_device)

    def erase_onboard_memory(self) -> dict:
        """
        Erase the active device's onboard flash.

        :return: The `erase_memory` response from sifibridge.
        :raises SifiBridgeError: If sifibridge returns an error response.
        """
        return self._request("erase-memory")

    def power_off(self) -> dict:
        """Power off the active device.

        :return: The response from sifibridge
        """
        return self._request("power-off")

    def set_led(self, index: int, on: bool) -> dict:
        """
        Open or close LED `index` on the active device.

        :param index: LED to set, either `1` or `2`.
        :param on: True to open (turn on), False to close (turn off).

        :raises ValueError: If `index` is not 1 or 2.
        :return: The `led` response payload.
        """
        if index not in (1, 2):
            raise ValueError(f"LED index must be 1 or 2, got {index}")
        s = "on" if on else "off"
        return self._request(f"led --state {s} {index}")["led"]

    def set_motor(self, on: bool) -> dict:
        """
        Start or stop the vibration motor on the active device.

        :return: Response from sifibridge
        """
        s = "on" if on else "off"
        return self._request(f"motor --state {s}")

    def set_status_updates(self, on: bool) -> dict:
        """
        Enable or stop status updates.

        Status updates are periodic (~1s) data packets containing information such as memory used, memory size, etc.

        :return: The `status_update` response payload.
        """
        s = "on" if on else "off"
        return self._request(f"status-update {s}")["status_update"]

    def start(self, all: bool = False) -> bool:
        """
        Start an acquisition.

        :param all: Start on all devices.

        :return: True if success
        """
        return self._request(f"start {'--all' if all else ''}")["start"]["connected"]

    def stop(self, all: bool = False) -> bool:
        """
        Stop acquisition.

        :param all: Start on all devices.

        :return: True if success
        """
        return self._request(f"stop {'--all' if all else ''}")["stop"]["connected"]

    def send_event(self, all: bool = False) -> dict:
        """
        Generate a software event.
        The event will appear in the data stream as an `event` packet.

        :param all: Start on all devices.

        :return: True if success
        """
        return self._request(f"event {'--all' if all else ''}")["event"]["connected"]

    def buffer_export(
        self,
        fmt: str = "csv",
        output_dir: str = ".",
        device: str | None = None,
    ) -> dict:
        """
        Export a device's buffered acquisitions to file.

        :param fmt: Output format. Either `csv` or `hdf5`.
        :param output_dir: Directory to save the exported data.
        :param device: Device ID or name. Defaults to the active device.
        """
        cmd_parts = ["buffer export"]
        if device is not None:
            cmd_parts.append(f"--handle {device}")
        cmd_parts.append(f"--dir {output_dir}")
        cmd_parts.append(f"--format {fmt}")
        return self._request(" ".join(cmd_parts))["buffer_export"]

    def buffer_list(self, device: str | None = None) -> list[dict]:
        """
        List buffered acquisitions and their status.

        :param device: Device ID or name to filter by. Defaults to all devices.
        :return: A list of acquisition info dicts, each with keys ``id``,
            ``device``, ``start_time``, ``completed``, ``sensors`` (a list of
            ``{"name", "num_samples"}``) and ``total_samples``.
        """
        cmd_parts = ["buffer list"]
        if device is not None:
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
            cmd_parts.append(f"--handle {device}")
        if acquisition_id is not None:
            cmd_parts.append(f"--id {acquisition_id}")
        if all:
            cmd_parts.append("--all")
        return self._request(" ".join(cmd_parts))["buffer_clear"]

    # ------------------------------------------------------------------
    # Core IO: one generic request/response, two workers, one data queue
    # ------------------------------------------------------------------

    def _request(self, line: str, timeout: float | None = None) -> dict:
        """
        Write one REPL line and read exactly one JSON response.

        :param line: REPL command (no trailing newline).
        :param timeout: Seconds to wait. Defaults to `_DEFAULT_REQUEST_TIMEOUT`.
        :return: Parsed response dict.
        :raises SifiBridgeTimeout: If sifibridge does not respond in time.
        :raises SifiBridgeError: On a `{"error": ...}` response from sifibridge.
        """
        if timeout is None:
            timeout = self._DEFAULT_REQUEST_TIMEOUT

        logger.debug(f"-> {line}")
        with self._response_lock:
            assert self._bridge.stdin is not None
            self._bridge.stdin.write(f"{line}\n".encode())
            self._bridge.stdin.flush()
            try:
                resp = self._response_queue.get(timeout=timeout)
            except queue.Empty:
                raise SifiBridgeTimeout(f"No response to {line!r} within {timeout}s")
        logger.debug(f"<- {resp}")
        if "error" in resp:
            raise SifiBridgeError(
                resp["error"].get("message", "Unknown sifibridge error")
            )
        return resp

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
