import subprocess as sp
import json
from collections.abc import Iterable
from enum import Enum
import select

import logging
from sifi_bridge_py import utils


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
    STATUS = "status"
    MEMORY = "memory"
    TEMPERATURE = "temperature"
    START_TIME = "start_time"
    LOW_LATENCY = "low_latency"


class PacketStatus(Enum):
    """
    Data packet statuses.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_ecg()
    >>> print(packet["status"] == PacketStatus.OK.value
    True
    ```
    """

    OK = "ok"
    LOST_DATA = "lost_data"
    RECORDING = "recording"
    MEMORY_DOWNLOAD_COMPLETED = "memory_download_completed"
    MEMORY_ERASED = "memory_erased"
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
    >>> qw = emg[SensorChannel.IMU.value[0]] # get first channel
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


class DeviceCommand(Enum):
    """
    Use in tandem with SifiBridge.send_command() to control Sifi device operation.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.send_command(DeviceCommand.OPEN_LED_1) # LED 1 is turned on
    ```
    """

    START_ACQUISITION = "start-acquisition"
    STOP_ACQUISITION = "stop-acquisition"
    SET_BLE_POWER = "set-ble-power"
    SET_ONBOARD_FILTERING = "set-filtering"
    ERASE_ONBOARD_MEMORY = "erase-memory"
    DOWNLOAD_ONBOARD_MEMORY = "download-memory"
    START_STATUS_UPDATE = "start-status-update"
    OPEN_LED_1 = "open-led1"
    OPEN_LED_2 = "open-led2"
    CLOSE_LED_1 = "close-led1"
    CLOSE_LED_2 = "close-led2"
    START_MOTOR = "start-motor"
    STOP_MOTOR = "stop-motor"
    POWER_OFF = "power-off"
    POWER_DEEP_SLEEP = "deep-sleep"
    SET_PPG_CURRENTS = "set-ppg-currents"
    SET_PPG_SENSITIVITY = "set-ppg-sensitivity"
    SET_EMG_MAINS_NOTCH = "set-emg-mains-notch"
    SET_EDA_FREQUENCY = "set-eda-freq"
    SET_EDA_GAIN = "set-eda-gain"
    DOWNLOAD_MEMORY_SERIAL = "download-memory-serial"
    STOP_STATUS_UPDATE = "stop-status-update"


class DeviceType(Enum):
    """
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
    Wrapper class over Sifi Bridge CLI tool. It is recommend to use it in a thread to avoid blocking on I/O.
    """

    _bridge: sp.Popen[bytes]
    """
    SiFi Bridge executable instance.
    """

    active_device: str

    def __init__(
        self, publishers: None | str | Iterable[str] = None, use_lsl: bool = False
    ):
        """
        Create a SiFi Bridge instance. Currently, only standard input is supported to interact with SiFi Bridge.

        The constructor first checks if `sifibridge` is present in the current working directory.
        If it is, it checks if its version is compatible with the Python package.
        If they are incompatible OR `sifibridge` is not already in the directory, the latest compatible version is downloaded from the official Github repository.
        If they are compatible, no matter the patch version, the existing `sifibridge` is used.

        For more documentation about SiFi Bridge, see `sifibridge -h` or the interactive help: `sifibridge; help`

        :param publishers: Use additional publishers. Leave empty to only use stdout. Otherwise any combination of {`"tcp://<ip>:<port>"`, `"udp://<ip>:<port>"`, `"csv://data/root/directory/"`}.
        :param use_lsl: If `True`, `sifibridge` will also stream sensor data to Lab Streaming Layer outlets. Refer to `sifibridge`'s `lsl` REPL command for more information.
        """

        executable = "./sifibridge"

        # Check if sifibridge in cwd
        try:
            cli_version = (
                sp.run([executable, "-V"], stdout=sp.PIPE)
                .stdout.decode()
                .strip()
                .split(" ")[-1]
            )
        except FileNotFoundError:
            cli_version = "0.0.1"
        py_version = utils._get_package_version()

        logging.debug(f"CLI version: {cli_version}, Python version: {py_version}")

        if not utils._are_compatible(cli_version, py_version):
            logging.info("Downloading latest compatible version of sifibridge.")
            executable = utils.get_sifi_bridge("./")

        exec_command = [executable]

        if publishers is not None:
            if isinstance(publishers, str):
                publishers = [publishers]
            for publisher in publishers:
                p, parg = publisher.split("://")
                exec_command.append(f"--{p}-out")
                exec_command.append(parg)

        if use_lsl:
            exec_command.append("--lsl")

        logging.info(f"Launching executable: {' '.join(exec_command)}")
        self._bridge = sp.Popen(
            exec_command, stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE
        )

        self.active_device = self.get_data()["new"]["active"]

    def show(self):
        """
        Get information about the current SiFi Bridge device.
        """
        self.__write("show")
        return self.get_data_with_key("show")["show"]

    def create_device(self, name: str, select: bool = True):
        """
        Create a device and optionally select it.

        :param name: Device name
        :param select: True to select the device after creation

        Raises a `ValueError` if `uid` contains spaces.

        :return: Response from Bridge
        """
        if " " in name:
            raise ValueError(f"Spaces are not supported in device name ({name})")

        old_active = self.active_device
        self.__write(f"new {name}")
        resp = self.get_data_with_key("new")
        self.active_device = resp["new"]["active"]
        if not select:
            return self.select_device(old_active)
        return resp

    def select_device(self, name: str):
        """
        Select a device.

        :param name: Name of the device to select

        :return: Response from SiFi Bridge
        """
        self.__write(f"select {name}")
        resp = self.get_data_with_key("select")
        self.active_device = resp["select"]["active"]
        return resp

    def delete_device(self, name: str):
        """
        Delete a device and selects another one.

        :param name: Name of the manager to delete

        :return: Response from SiFi Bridge
        """
        self.__write(f"delete {name}")
        return self.get_data_with_key("delete")["delete"]["active"]

    def list_devices(self, source: ListSources | str) -> list[str]:
        """
        List all devices found from a given `source`.

        :return: Response from SiFi Bridge
        :raises ConnectionError: If Bluetooth is off.
        """
        if isinstance(source, str):
            source = ListSources(source)
        self.__write(f"list {source.value}")
        self.__check_stderr_for_bluetooth_err()
        return self.get_data_with_key("list")["list"]["devices"]

    def connect(self, handle: DeviceType | str | None = None) -> bool:
        """
        Try to connect to `handle`.

        :param handle: Device handle to connect to. Can be:

            - `None` to connect to any device
            - a `DeviceType` to connect by device name
            - a MAC (Windows/Linux) / UUID (MacOS) to connect to a specific device.

        :return: Connection status
        :raises ConnectionError: If Bluetooth is off.
        """

        if isinstance(handle, DeviceType):
            handle = handle.value

        self.__write(f"connect {handle if handle is not None else ''}")
        self.__check_stderr_for_bluetooth_err()
        ret = self.get_data_with_key("connect")["connect"]["connected"]
        if ret is False:
            logging.info(f"Could not connect to {handle}")
        return ret

    def disconnect(self):
        """
        Disconnect from the active device.

        :return: Connection status response
        """
        self.__write("disconnect")
        ret = self.get_data_with_key("disconnect")["disconnect"]["connected"]
        return ret

    def set_filters(self, enable: bool):
        """
        Set state of onboard filtering for all sensors.

        :return: Configuration response
        """
        self.__write(f"configure filtering {'on' if enable else 'off'}")
        return self.get_data_with_key("configure")

    def configure_sensors(
        self,
        ecg: bool = False,
        emg: bool = False,
        eda: bool = False,
        imu: bool = False,
        ppg: bool = False,
    ):
        """
        Configure the enabled sensors.

        :param ecg: True to enable ECG, False to disable
        :param emg: True to enable EMG, False to disable
        :param eda: True to enable EDA, False to disable
        :param imu: True to enable IMU, False to disable
        :param ppg: True to enable PPG, False to disable

        :return: Configuration response
        """
        cmd_parts = ["configure sensors"]
        cmd_parts.append(f"--ecg {'on' if ecg else 'off'}")
        cmd_parts.append(f"--emg {'on' if emg else 'off'}")
        cmd_parts.append(f"--eda {'on' if eda else 'off'}")
        cmd_parts.append(f"--imu {'on' if imu else 'off'}")
        cmd_parts.append(f"--ppg {'on' if ppg else 'off'}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def set_ble_power(self, power: BleTxPower | str):
        """
        Set the BLE transmission power.

        :param power: Device transmission power level to set

        :return: Configuration response
        """
        if isinstance(power, str):
            power = BleTxPower(power)

        self.__write(f"configure ble-power {power.value}")
        return self.get_data_with_key("configure")

    def set_memory_mode(self, memory_config: MemoryMode | str):
        """
        Configure the device's memory mode.

        **NOTE**: See `MemoryMode` for more information.

        :param memory_config: Memory mode to set

        :return: Configuration response
        """
        if isinstance(memory_config, str):
            memory_config = MemoryMode(memory_config)

        self.__write(f"configure memory {memory_config.value}")
        return self.get_data_with_key("configure")

    def configure_ecg(
        self,
        state: bool = True,
        fs: int = 500,
        dc_notch: bool = True,
        mains_notch: None | int = 50,
        bandpass: bool = True,
        flo: float = 0,
        fhi: float = 30,
    ) -> dict:
        """
        Configure ECG sensor.

        :param state: Enable/disable ECG sensor (True/False)
        :param fs: Sampling rate in Hz (e.g., 250, 500, 1000)
        :param dc_notch: Enable/disable DC offset removal filter
        :param mains_notch: Mains frequency notch filter. Options: None (off), 50 (50 Hz), 60 (60 Hz)
        :param bandpass: Enable/disable bandpass filter
        :param flo: Lower cutoff frequency for bandpass filter (Hz)
        :param fhi: Higher cutoff frequency for bandpass filter (Hz)

        :return: Configuration response
        """
        cmd_parts = ["configure ecg"]

        cmd_parts.append(f"--state {'on' if state else 'off'}")
        cmd_parts.append(f"--fs {fs}")
        cmd_parts.append(f"--dc-notch {'on' if dc_notch else 'off'}")
        if mains_notch == 50:
            cmd_parts.append("--mains-notch on50")
        elif mains_notch == 60:
            cmd_parts.append("--mains-notch on60")
        else:
            cmd_parts.append("--mains-notch off")
        cmd_parts.append(f"--bandpass {'on' if bandpass else 'off'}")
        cmd_parts.append(f"--flo {flo}")
        cmd_parts.append(f"--fhi {fhi}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def configure_emg(
        self,
        state: bool = True,
        fs: int = 2000,
        dc_notch: bool = True,
        mains_notch: None | int = 50,
        bandpass: bool = True,
        flo: float = 20,
        fhi: float = 450,
    ) -> dict:
        """
        Configure EMG sensor.

        :param state: Enable/disable EMG sensor (True/False)
        :param fs: Sampling rate in Hz (e.g., 1000, 2000)
        :param dc_notch: Enable/disable DC offset removal filter
        :param mains_notch: Mains frequency notch filter. Options: None (Off), 50 (50 Hz), 60 (60 Hz)
        :param bandpass: Enable/disable bandpass filter
        :param flo: Lower cutoff frequency for bandpass filter (Hz)
        :param fhi: Higher cutoff frequency for bandpass filter (Hz)

        :return: Configuration response
        """
        cmd_parts = ["configure emg"]

        cmd_parts.append(f"--state {'on' if state else 'off'}")
        cmd_parts.append(f"--fs {fs}")
        cmd_parts.append(f"--dc-notch {'on' if dc_notch else 'off'}")
        if mains_notch == 50:
            cmd_parts.append("--mains-notch on50")
        elif mains_notch == 60:
            cmd_parts.append("--mains-notch on60")
        else:
            cmd_parts.append("--mains-notch off")
        cmd_parts.append(f"--bandpass {'on' if bandpass else 'off'}")
        cmd_parts.append(f"--flo {flo}")
        cmd_parts.append(f"--fhi {fhi}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def configure_eda(
        self,
        state: bool = True,
        fs: int = 50,
        dc_notch: bool = True,
        mains_notch: int | None = 50,
        bandpass: bool = True,
        flo: float = 0,
        fhi: float = 5,
        freq: int = 0,
    ) -> dict:
        """
        Configure EDA/BIOZ sensor. **Warning**: Enabling BIOZ and ECG/EMG at the same time
        is not recommended as it may cause interference and degrade the quality of ECG/EMG data.

        :param state: Enable/disable EDA sensor
        :param fs: Sampling rate in Hz (e.g., 50, 100
        :param dc_notch: Set DC offset removal filter
        :param mains_notch: Mains frequency notch filter. Options: None (Off), 50 (50 Hz), 60 (60 Hz)
        :param bandpass: Enable/disable bandpass filter
        :param flo: Lower cutoff frequency for bandpass filter (Hz)
        :param fhi: Higher cutoff frequency for bandpass filter (Hz)
        :param freq: EDA/BIOZ excitation signal frequency (Hz). 0 for DC measurement

        :return: Configuration response
        """
        cmd_parts = ["configure eda"]

        cmd_parts.append(f"--state {'on' if state else 'off'}")
        cmd_parts.append(f"--fs {fs}")
        cmd_parts.append(f"--dc-notch {'on' if dc_notch else 'off'}")
        if mains_notch == 50:
            cmd_parts.append("--mains-notch on50")
        elif mains_notch == 60:
            cmd_parts.append("--mains-notch on60")
        else:
            cmd_parts.append("--mains-notch off")
        cmd_parts.append(f"--bandpass {'on' if bandpass else 'off'}")
        cmd_parts.append(f"--flo {flo}")
        cmd_parts.append(f"--fhi {fhi}")
        cmd_parts.append(f"--freq {freq}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def configure_ppg(
        self,
        state: bool = True,
        fs: int = 100,
        ir: int = 9,
        red: int = 9,
        green: int = 9,
        blue: int = 9,
        sens: PpgSensitivity | str = PpgSensitivity.MEDIUM,
        avg: int = 1,
    ) -> dict:
        """
        Configure PPG sensor.

        :param state: enable PPG sensor
        :param fs: sampling rate in Hz [50, 100, 200, 400, 800, 1000, 1600, 3200]
        :param ir: current of IR LED in mA (1-50)
        :param r: current of R LED in mA (1-50)
        :param g: current of G LED in mA (1-50)
        :param b: current of B LED in mA (1-50)
        :param sens: light sensor sensitivity. See `PpgSensitivity` for more information
        :param avg: signal averaging factor for a smoother but less responsive signal

        :return: Configuration response
        """
        if isinstance(sens, str):
            sens = PpgSensitivity(sens)

        cmd_parts = ["configure ppg"]

        cmd_parts.append(f"--state {'on' if state else 'off'}")
        cmd_parts.append(f"--fs {fs}")
        cmd_parts.append(f"--iir {ir}")
        cmd_parts.append(f"--ired {red}")
        cmd_parts.append(f"--igreen {green}")
        cmd_parts.append(f"--iblue {blue}")
        cmd_parts.append(f"--sens {sens.value}")
        cmd_parts.append(f"--avg {avg}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def configure_imu(
        self,
        state: bool = True,
        fs: int = 100,
        accel_range: int = 2,
        gyro_range: int = 16,
    ) -> dict:
        """
        Configure IMU sensor.

        :param state: enable IMU sensor
        :param fs: sampling rate in Hz [50, 100, 200, 400, 800, 1000, 1600, 3200]
        :param ir: current of IR LED in mA (1-50)
        :param r: current of R LED in mA (1-50)
        :param g: current of G LED in mA (1-50)
        :param b: current of B LED in mA (1-50)
        :param sens: light sensor sensitivity. See `PpgSensitivity` for more information.

        :return: Configuration response
        """

        cmd_parts = ["configure imu"]

        cmd_parts.append(f"--state {'on' if state else 'off'}")
        cmd_parts.append(f"--fs {fs}")
        cmd_parts.append(f"--acc-range {accel_range}")
        cmd_parts.append(f"--gyro-range {gyro_range}")

        self.__write(" ".join(cmd_parts))
        return self.get_data_with_key("configure")

    def configure_sampling_freqs(self, ecg=500, emg=2000, eda=50, imu=100, ppg=100):
        """
        Configure the sampling frequencies [Hz] of biosignal acquisition.

        :return: Configuration response
        """
        self.__write(
            f"configure sampling-rates --ecg {ecg} --emg {emg} --eda {eda} --imu {imu} --ppg {ppg}"
        )
        return self.get_data_with_key("configure")

    def set_low_latency_mode(self, on: bool):
        """
        Set the low latency data mode.

        **NOTE**: Only supported on select BioPoint versions. Ask SiFi Labs directly if you need to use this feature.

        :param on: True to use low latency mode, in which packets are sent much faster with data from every sensor as it comes in. False to use the conventional 1 biosignal-batch-per-packet (default)

        :return: Configuration response
        """
        streaming = "on" if on else "off"
        self.__write(f"configure low-latency-mode {streaming}")
        return self.get_data_with_key("configure")

    def set_stealth_mode(self, enable: bool):
        """
        Enable or disable stealth mode. When enabled, device LEDs are disabled during acquisition.

        Useful for:
        - Sleep studies: Minimize light disturbance
        - Covert monitoring: Reduce visual indicators
        - Power saving: Disable LEDs to extend battery life

        :param enable: True to enable stealth mode, False to disable

        :return: Configuration response
        """
        state = "on" if enable else "off"
        self.__write(f"configure stealth-mode {state}")
        return self.get_data_with_key("configure")

    def set_motor_intensity(self, level: int):
        """
        Set the vibration motor intensity level.

        Useful for:
        - Haptic feedback in biofeedback applications
        - Event markers in experiments
        - Alert notifications for threshold crossing

        :param level: Intensity level (1-10), where 10 is maximum intensity

        :return: Configuration response
        :raises ValueError: If level is not between 1 and 10
        """
        if not 1 <= level <= 10:
            raise ValueError(
                f"Motor intensity level must be between 1 and 10, got {level}"
            )

        self.__write(f"configure motor-intensity {level}")
        return self.get_data_with_key("configure")

    def start_memory_download(self) -> int:
        """
        Start downloading the data stored on BioPoint's onboard memory. Depending on the output transport, the Python wrapper shall:
            - Continuously `self.get_data()` if the transport is `stdout` (default)
            - Wait for a packet of type `"memory"` with the `"status"` key set to `"MemoryDownloadCompleted"`.

        :return: Number of kilobytes to download.

        :raise ConnectionError: If the device is not connected.
        :raise TypeError: If the device does not support memory download.
        """
        if not self.show()["connected"]:
            raise ConnectionError(f"{self.active_device} is not connected")

        self.send_command(DeviceCommand.START_STATUS_UPDATE)
        kb_to_download = None
        while True:
            data = self.get_data()
            if data["id"] != self.active_device or data["packet_type"] != "status":
                continue
            if "memory_used_kbytes" not in data["data"].keys():
                raise TypeError(
                    f"Attempted to download memory from an unsupported device ({data['device']})."
                )
            kb_to_download = data["data"]["memory_used_kbytes"][0]
            break

        logging.info(f"kB to download: {kb_to_download}")

        self.send_command(DeviceCommand.DOWNLOAD_ONBOARD_MEMORY)

        return kb_to_download

    def send_command(self, command: DeviceCommand | str) -> bool:
        """
        Send a command to active device.

        :param command: Command to send

        :return: True if command was sent successfully, False otherwise.
        """
        if isinstance(command, str):
            command = DeviceCommand(command)

        self.__write(f"command {command.value}")
        return self.get_data_with_key("command")["command"]["connected"]

    def start(self) -> bool:
        """
        Start an acquisition.

        :return: True if command was sent successfully, False otherwise.

        :raise ConnectionError: If unable to send the command, e.g. if disconnected.

        """
        return self.send_command(DeviceCommand.START_ACQUISITION)

    def stop(self) -> bool:
        """
        Stop acquisition. Does not wait for confirmation, so ensure there is enough time (~1s) for the command to reach the BLE device before destroying Sifi Bridge instance.

        :return: True if command was sent successfully, False otherwise.
        """
        return self.send_command(DeviceCommand.STOP_ACQUISITION)

    def get_data(self, timeout: float | None = None) -> dict:
        """
        Wait for Bridge to return a packet.

        :param timeout: Time in seconds to wait for a packet. If `None`, will block indefinitely.

        :return: Packet as a dictionary.
        """
        ready_to_read, _, _ = select.select([self._bridge.stdout], [], [], timeout)
        ret = {}
        if self._bridge.stdout in ready_to_read:
            packet = self._bridge.stdout.readline().decode()
            ret = json.loads(packet)
        return ret

    def get_data_with_key(self, keys: str | Iterable[str]) -> dict:
        """
        Wait for Bridge to return a packet with a specific key. Blocks until a packet is received and returns it as a dictionary.

        :param key: Key to wait for. If a string, will wait until the key is found. If an iterable, will wait until all keys are found.

        :return: Packet with the requested key(s) as a dictionary.
        """
        ret = dict()
        if isinstance(keys, str):
            while keys not in ret.keys():
                ret = self.get_data()
        elif isinstance(keys, Iterable):
            while True:
                is_ok = False
                ret = self.get_data()
                tmp = ret.copy()
                for i, k in enumerate(keys):
                    if k not in tmp.keys():
                        break
                    elif i == len(keys) - 1:
                        is_ok = True
                    else:
                        tmp = ret[k]
                if is_ok:
                    break
        return ret

    def get_ecg(self):
        """
        Wait for an ECG packet.

        :return: ECG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == PacketType.ECG.value:
                return data

    def get_emg(self):
        """
        Wait for an EMG packet.

        :return: EMG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] in (
                PacketType.EMG.value,
                PacketType.EMG_ARMBAND.value,
            ):
                return data

    def get_eda(self):
        """
        Wait for an EDA packet.

        :return: EDA data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == PacketType.EDA.value:
                return data

    def get_imu(self):
        """
        Wait for an IMU packet.

        :return: IMU data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == PacketType.IMU.value:
                return data

    def get_ppg(self):
        """
        Wait for a PPG packet.

        :return: PPG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == PacketType.PPG.value:
                return data

    def get_temperature(self):
        """
        Wait for a temperature packet.

        :return: Temperature data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == PacketType.TEMPERATURE.value:
                return data

    def is_memory_download_completed(self, packet: dict) -> bool:
        """
        Helper function to check if `packet` indicates that memory download is finished, i.e., `packet["status"] == "memory_download_completed"`.


        :return: True if memory download is finished, False otherwise.
        """
        if (
            packet["packet_type"] == PacketType.MEMORY.value
            and packet["status"] == PacketStatus.MEMORY_DOWNLOAD_COMPLETED.value
        ):
            return True
        else:
            return False

    def __check_stderr_for_bluetooth_err(self):
        """Check if there is any error message from SiFi Bridge's stderr. If there is, assume it's a BLE error.

        Raises:
            ConnectionError: If BLE is off.
        """
        ready_to_read, _, _ = select.select([self._bridge.stderr], [], [], 0.1)

        if self._bridge.stderr in ready_to_read:
            logging.error(self._bridge.stderr.readline().decode())
            raise ConnectionError("Bluetooth is off.")

    def __write(self, cmd: str):
        """Write some data to SiFi Bridge's stdin.

        :param cmd: Message to write.
        """
        logging.info(cmd)
        self._bridge.stdin.write((f"{cmd}\n").encode())
        self._bridge.stdin.flush()

    def __del__(self):
        self.__write("quit")
        self._bridge.wait()
