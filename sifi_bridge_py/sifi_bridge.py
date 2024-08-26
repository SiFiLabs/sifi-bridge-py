import subprocess as sp
import json
from typing import Iterable
from enum import Enum

import logging

from sifi_bridge_py import utils


class DeviceCommand(Enum):
    """
    Use in tandem with SifiBridge.send_command() to control Sifi device operation.
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
    BIOPOINT_V1_4 = "BioPoint_v1_4"
    BIOARMBAND = "BioArmband"


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
    Sets how the device should deal with data storage.

    - `STREAMING` streams data to the host computer via BLE
    - `DEVICE` saves the data stream to on-board flash
    - `BOTH` does both

    **NOTE**: BioArmband does not support on-board memory (`DEVICE` variant).
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
    MANAGERS = "managers"


class SifiBridge:
    """
    Wrapper class over Sifi Bridge CLI tool. It is recommend to use it in a thread to avoid blocking on I/O.
    """

    _bridge: sp.Popen
    """
    SiFi Bridge executable instance.
    """

    active_device: str

    def __init__(self, exec_path: str = "sifibridge"):
        """
        Create a SiFi Bridge instance. Currently, only `stdin` and `stdout` are supported to communicate with Sifi Bridge.

        For more documentation about SiFi Bridge, see `sifibridge -h` or the interactive help: `sifibridge; help;̀

        :param exec_path: Path to `sifibridge`. If it is in `$PATH`, you can leave it at the default value.
        """
        cli_version = (
            sp.run([exec_path, "-V"], stdout=sp.PIPE)
            .stdout.decode()
            .strip()
            .split(" ")[-1]
        )
        py_version = utils.get_package_version()

        assert cli_version[0:3] == py_version[0:3], (
            f"Version mismatch between sifi_bridge_py ({py_version}) and {exec_path} ({cli_version}). "
            "Please ensure both have the same major and minor versions. "
            "See sifi_bridge_py.utils.get_sifi_bridge() to fetch the corresponding version."
        )

        self._bridge = sp.Popen([exec_path], stdin=sp.PIPE, stdout=sp.PIPE)
        self.active_device = self.show()["id"]

    def show(self):
        """
        Get information about the current SiFi Bridge device.
        """
        self.__write("show")
        return self.get_data_with_key("ble_power")

    def create_manager(self, name: str, select: bool = True):
        """
        Create a manager and optionally select it.

        :param name: Manager name
        :param select: True to select the manager after creation

        Raises a `ValueError` if `uid` contains spaces.

        :return: Response from Bridge
        """
        if " " in name:
            raise ValueError(f"Spaces are not supported in device name ({name})")

        old_active = self.active_device
        self.__write(f"new {name}")
        resp = self.get_data_with_key("active")
        self.active_device = resp["active"]
        if not select:
            return self.select_manager(old_active)
        return resp

    def select_manager(self, name: str):
        """
        Select a manager as active.

        :param name: Name of the manager to select

        :return: Response from SiFi Bridge
        """
        self.__write(f"select {name}")
        resp = self.get_data_with_key("active")
        self.active_device = resp["active"]
        return resp

    def delete_manager(self, name: str):
        """
        Delete a manager and selects another one.

        :param name: Name of the manager to delete

        :return: Response from SiFi Bridge
        """
        self.__write(f"delete {name}")
        return self.get_data_with_key("active")

    def list_devices(self, source: ListSources) -> dict:
        """
        List all devices found from a given `source`.

        :return: Response from SiFi Bridge
        """
        self.__write(f"list {source.value}")
        return self.get_data_with_key("found_devices")

    def connect(self, handle: DeviceType | str) -> bool:
        """
        Try to connect to `handle`.

        :param handle: Device handle to connect to. Can be a `DeviceType` to connect by device name or a MAC (Windows/Linux) / UUID (MacOS) to connect to a specific device.

        :return: Connection status
        """

        if isinstance(handle, DeviceType):
            handle = handle.value

        self.__write(f"connect {handle}")
        ret = self.get_data_with_key("connected")["connected"]
        if ret is False:
            logging.info(f"Could not connect to {handle}")
        return ret

    def disconnect(self):
        """
        Disconnect from the active device.

        :return: Connection status response
        """
        self.__write("disconnect")
        ret = self.get_data_with_key("connected")
        return ret

    def set_filters(self, enable: bool):
        """
        Set state of onboard filtering for all biochannels.

        :return: Configuration response
        """
        self.__write(f"configure filtering {'on' if enable else 'off'}")
        return self.get_data_with_key("configure")

    def set_channels(
        self,
        ecg: bool = False,
        emg: bool = False,
        eda: bool = False,
        imu: bool = False,
        ppg: bool = False,
    ):
        """
        Select which biochannels to enable.

        :return: Configuration response
        """
        ecg = "on" if ecg else "off"
        emg = "on" if emg else "off"
        eda = "on" if eda else "off"
        imu = "on" if imu else "off"
        ppg = "on" if ppg else "off"

        self.__write(f"configure channels {ecg} {emg} {eda} {imu} {ppg}")
        return self.get_data_with_key("configure")

    def set_ble_power(self, power: BleTxPower):
        """
        Set the BLE transmission power.

        :param power: Device transmission power level to set

        :return: Configuration response
        """
        self.__write(f"configure ble-power {power.value}")
        return self.get_data_with_key("configure")

    def set_memory_mode(self, memory_config: MemoryMode):
        """
        Configure the device's memory mode.

        **NOTE**: See `MemoryMode` for more information.

        :param memory_config: Memory mode to set

        :return: Configuration response
        """
        self.__write(f"configure memory {memory_config.value}")
        return self.get_data_with_key("configure")

    def configure_emg(self, bandpass_freqs: tuple = (20, 450), notch_freq: int = 50):
        """
        Configure EMG biochannel filters. Also calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :notch_freq: Mains notch filter frequency. {50, 60} Hz, otherwise notch is disabled.

        :return: Configuration response
        """
        self.set_filters(True)
        self.__write(
            f"configure emg {bandpass_freqs[0]} {bandpass_freqs[1]} {notch_freq}"
        )
        return self.get_data_with_key("configure")

    def configure_ecg(self, bandpass_freqs: tuple = (0, 30)):
        """
        Configure ECG biochannel filters. Also calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.

        :return: Configuration response
        """
        self.set_filters(True)
        self.__write(f"configure ecg {bandpass_freqs[0]} {bandpass_freqs[1]}")
        return self.get_data_with_key("configure")

    def configure_eda(
        self,
        bandpass_freqs: tuple = (0, 5),
        signal_freq: int = 0,
    ):
        """
        Configure EDA biochannel. Also calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :signal_freq: frequency of EDA excitation signal. 0 for DC.

        :return: Configuration response
        """
        self.set_filters(True)
        self.__write(
            f"configure eda {bandpass_freqs[0]} {bandpass_freqs[1]} {signal_freq}"
        )
        return self.get_data_with_key("configure")

    def configure_ppg(
        self,
        ir: int = 9,
        red: int = 9,
        green: int = 9,
        blue: int = 9,
        sens: PpgSensitivity = PpgSensitivity.MEDIUM,
    ):
        """
        Configure PPG biochannel. Internally calls `self.set_filters(True)`.

        :param ir, red, green, blue: current of each PPG LED in mA (1-50)
        :param sens: light sensor sensitivity. See `PpgSensitivity` for more information.

        :return: Configuration response
        """

        self.__write(f"configure ppg {ir} {red} {green} {blue} {sens}")
        return self.get_data_with_key("configure")

    def configure_sampling_freqs(self, ecg=500, emg=2000, eda=40, imu=50, ppg=50):
        """
        Configure the sampling frequencies [Hz] of biosignal acquisition.

        NOTE: Currently unused.

        :return: Configuration response
        """
        self.__write(f"configure sampling-rates {ecg} {emg} {eda} {imu} {ppg}")
        return self.get_data_with_key("configure")

    def set_low_latency_mode(self, streaming: bool):
        """
        Set the low latency data mode.

        **NOTE**: Only supported on select BioPoint versions. Ask SiFi Labs directly if you need to use this feature.

        :mode: True to use low latency mode, in which packets are sent much faster with data from every biochannels as it comes in. False to use the conventional 1 biosignal-batch-per-packet (default)

        :return: Configuration response
        """
        streaming = "on" if streaming else "off"
        self.__write(f"configure low-latency-mode {streaming}")
        return self.get_data_with_key("configure")

    def start_memory_download(self, show_progress: bool) -> int:
        """
        Start downloading the data stored on BioPoint's onboard memory.
        It is up to the user to then continuously `wait_for_data` and manage how to store the data (to file, to Python object, etc).

        :param show_progress: If True, will return the number of kilobytes to download. If False, will return -1.

        :return: Number of kilobytes to download. -1 if show_progress is False. -2 if an error happened.
        """
        kb_to_download = -1

        if not self.show()["connected"]:
            logging.warning(f"{self.active_device} is not connected")
            return -2

        if show_progress:
            self.send_command(DeviceCommand.START_STATUS_UPDATE)
            while True:
                try:
                    data = self.get_data_with_key(["data", "memory_used_kb"])
                    if data["id"] != self.active_device:
                        continue
                    kb_to_download = data["data"]["memory_used_kb"]
                    break
                except KeyError:
                    continue

            logging.info(f"KB to download: {kb_to_download}")

        self.send_command(DeviceCommand.DOWNLOAD_ONBOARD_MEMORY)

        return kb_to_download

    def send_command(self, command: DeviceCommand) -> bool:
        """
        Send a command to active device.

        Refer to SifiCommands enum for possible values.

        :return: True if command was sent successfully, False otherwise.
        """
        self.__write(f"command {command.value}")
        return self.get_data_with_key("command")["connected"]

    def start(self) -> dict:
        """
        Start an acquisition.

        :return: "Start Time" packet. Returns an empty dict if unable to start.
        """
        if not self.send_command(DeviceCommand.START_ACQUISITION):
            logging.error("Could not start acquisition")
            return {}
        while True:
            # Wait for start time packet
            resp = self.get_data_with_key(["packet_type"])
            if resp["packet_type"] != "start_time":
                continue
            logging.info(f"Started acquisition: {resp['data']}")
            return resp

    def stop(self):
        """
        Stop acquisition. Does not wait for confirmation, so ensure there is enough time (~1s) for the command to reach the BLE device before destroying Sifi Bridge instance.

        :return: True if command was sent successfully, False otherwise.
        """
        return self.send_command(DeviceCommand.STOP_ACQUISITION)

    def get_data(self) -> dict:
        """
        Wait for Bridge to return a packet. Blocking operation.

        :return: Packet as a dictionary.
        """

        ret = dict()
        try:
            ret = json.loads(self._bridge.stdout.readline().decode())
        except Exception as e:
            logging.error(f"{e}\n{ret}")
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
        Get ECG data.

        :return: ECG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "ecg":
                return data

    def get_emg(self):
        """
        Get EMG data.

        :return: EMG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] in ["emg", "emg_armband"]:
                return data

    def get_eda(self):
        """
        Get EDA data.

        :return: EDA data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "eda":
                return data

    def get_imu(self):
        """
        Get IMU data.

        :return: IMU data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "imu":
                return data

    def get_ppg(self):
        """
        Get PPG data.

        :return: PPG data packet as a dictionary.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "ppg":
                return data

    def __write(self, cmd: str):
        """Write some data to SiFi Bridge's stdin.

        :param cmd: Message to write.
        """
        logging.info(cmd)
        self._bridge.stdin.write((f"{cmd}\n").encode())
        self._bridge.stdin.flush()
