import subprocess as sp

import json
from typing import Iterable

from enum import Enum
from dataclasses import dataclass

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
    OPEN_LED_1 = "open-led-1"
    OPEN_LED_2 = "open-led-2"
    CLOSE_LED_1 = "close-led-1"
    CLOSE_LED_2 = "close-led-2"
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


class BlePower(Enum):
    """
    Use in tandem with SifiBridge.set_ble_power() to set the BLE transmission power.

    Higher transmission power will increase power consumption, but may improve connection stability.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MemoryMode(Enum):
    """
    Sets how the device should deal with data storage. `HOST` streams data to the host computer via BLE. `DEVICE` saves the data stream to on-board flash. `BOTH` does both.

    **NOTE**: BioArmband does not support on-board memory (`DEVICE` variant).
    """

    HOST = "host"
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


@dataclass
class _Device:
    uid: str
    """
    User ID of the device. This ID is set by the user to easily identify each SiFi devices.
    """
    name: str
    """
    BLE name of the device. This is set by the device itself and is used to connect to it.
    """
    connected: bool
    """
    Connection status of the device. True if connected, False otherwise.
    """


class ListSources(Enum):
    """
    Use in tandem with SifiBridge.list_devices() to list devices from different sources.
    """

    PY = "self"
    BLE = "ble"
    SERIAL = "serial"
    DEVICES = "devices"


class SifiBridge:
    """
    Wrapper class over Sifi Bridge CLI tool. It is recommend to use it in a thread to avoid blocking on IO.
    """

    bridge: sp.Popen
    """
    SiFi Bridge executable instance, you shouldn't have to manually interact with it.
    """

    active_device: str
    """
    Currently active SiFi Bridge Device.
    """

    devices: dict[str:_Device]
    """
    SiFi Bridge devices. Used to keep track of state and cache some informations.
    """

    def __init__(self, exec_path: str = "sifi_bridge"):
        """
        Create a SiFi Bridge instance. Currently, only `stdin` and `stdout` are supported to communicate with Sifi Bridge.

        :param exec_path: Path to sifi_bridge. If executable is in PATH, you can leave it at default value.
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

        self.bridge = sp.Popen([exec_path], stdin=sp.PIPE, stdout=sp.PIPE)
        self.active_device = "default"
        self.devices = {
            self.active_device: _Device(self.active_device, "device-1", False)
        }

    def show(self):
        """
        Get information about the current SiFi Bridge device.
        """
        self.__write("show")
        return self.get_data_with_key("ble_power")

    def create_device(self, name: str, select: bool = True) -> bool:
        """
        Create a SiFi Bridge device named `uid` and optionally select it.

        :param uid: User-defined name of the device
        :param select: Automatically select the device after creation

        Raises a `ValueError` if `uid` contains spaces.

        Returns True if the device was created, False otherwise.
        """
        if " " in name:
            raise ValueError(f"Spaces are not supported in device name ({name})")

        ret = False
        self.__write(f"create {name}")
        if name in self.list_devices("devices")["found_devices"]:
            self.devices[name] = _Device(name, "device1", False)
            ret = True
        if select:
            self.select_device(name)

        return ret

    def select_device(self, uid: str) -> bool:
        """
        Select the SiFi Bridge device with user id `uid`.

        Returns True if device was selected, False if it does not exist.
        """
        if uid in self.list_devices("devices")["found_devices"]:
            self.__write(f"select {uid}")
            self.active_device = uid
            return True
        return False

    def delete_device(self):
        """
        Delete the active device.
        """
        self.__write("delete")
        if len(self.devices) > 0:
            self.devices.pop(self.active_device)
        else:
            logging.warning("No devices to delete")

    def list_devices(self, source: ListSources) -> dict:
        """
        Returns all devices found from the `source`.

        :param source: "self" to list UID devices, "ble" to list BLE devices, "serial" for serial, and any other input will list the SiFi Bridge devices
        """
        self.__write(f"list {source.value}")
        sb_devs = self.get_data_with_key("found_devices")
        return sb_devs

    def connect(self, handle: DeviceType | str) -> bool:
        """
        Try to connect to `handle`.

        :param handle: Device handle to connect to. Can be a `DeviceType` to connect by device name or a MAC string to connect to a specific device.

        :return: True if connection successful, False otherwise.
        """

        if isinstance(handle, DeviceType):
            handle = handle.value

        self.__write(f"connect {handle}")
        ret = self.get_data_with_key("connected")
        self.devices[self.active_device].connected = ret["connected"]
        if ret["connected"] is True:
            self.devices[self.active_device].name = handle
            return True
        else:
            logging.info(f"Could not connect to {handle}")
        return False

    def disconnect(self):
        """
        Disconnect from the active device.
        """
        self.__write("disconnect")
        ret = self.get_data_with_key("connected")
        self.devices[self.active_device].connected = ret["connected"]
        return ret["connected"]

    def set_filters(self, enable: bool):
        """
        Set state of onboard filtering for all biochannels.
        """
        self.__write(f"configure filtering {'on' if enable else 'off'}")
        return self

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
        """
        ecg = "on" if ecg else "off"
        emg = "on" if emg else "off"
        eda = "on" if eda else "off"
        imu = "on" if imu else "off"
        ppg = "on" if ppg else "off"

        self.__write(f"configure channels {ecg} {emg} {eda} {imu} {ppg}")
        return self

    def set_ble_power(self, power: BlePower):
        """
        Set the BLE transmission power.

        :param power: Device transmission power level to set
        """
        self.__write(f"configure ble-power {power.value}")
        return self

    def set_memory_mode(self, memory_config: MemoryMode):
        """
        Configure the device's memory mode.

        **NOTE**: See `MemoryMode` for more information.

        :param memory_config: Memory mode to set
        """
        self.__write(f"configure memory {memory_config.value}")
        return self

    def configure_emg(self, bandpass_freqs: tuple = (20, 450), notch_freq: int = 50):
        """
        Configure EMG biochannel filters. Internally calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :notch_freq: Mains notch filter frequency. {50, 60} Hz, otherwise notch is disabled.
        """
        self.set_filters(True)
        self.__write(
            f"configure emg {bandpass_freqs[0]} {bandpass_freqs[1]} {notch_freq}"
        )
        return self

    def configure_ecg(self, bandpass_freqs: tuple = (0, 30)):
        """
        Configure ECG biochannel filters. Internally calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        """
        self.set_filters(True)
        self.__write(f"configure ecg {bandpass_freqs[0]} {bandpass_freqs[1]}")
        return self

    def configure_eda(
        self,
        bandpass_freqs: tuple = (0, 5),
        signal_freq: int = 0,
    ):
        """
        Configure EDA biochannel. Internally calls `self.set_filters(True)`.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :signal_freq: frequency of EDA excitation signal. 0 for DC.
        """
        self.set_filters(True)
        self.__write(
            f"configure eda {bandpass_freqs[0]} {bandpass_freqs[1]} {signal_freq}"
        )
        return self

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
        """

        self.__write(f"configure ppg {ir} {red} {green} {blue} {sens}")
        return self

    def configure_sampling_freqs(self, ecg=500, emg=2000, eda=40, imu=50, ppg=50):
        """
        Configure the sampling frequencies [Hz] of biosignal acquisition.

        NOTE: Currently unused.
        """
        self.__write(f"configure sampling-rates {ecg} {emg} {eda} {imu} {ppg}")
        return self

    def set_streaming_mode(self, streaming: bool):
        """
        Set the data delivery API mode. NOTE: Only supported on select BioPoint versions.

        :mode: True to use Low Latency mode, in which packets are sent much faster with data from all biosignals at once. False to use the conventional 1 biosignal-batch-per-packet (default)
        """
        streaming = "on" if streaming else "off"
        self.__write(f"configure streaming-mode {streaming}")
        return self

    def start_memory_download(self, show_progress: bool) -> int:
        """
        Start downloading the data stored on BioPoint's onboard memory.
        It is up to the user to then continuously `wait_for_data` and manage how to store the data (to file, to Python object, etc).

        :param show_progress: If True, will return the number of kilobytes to download. If False, will return -1.

        :return: Number of kilobytes to download. -1 if show_progress is False. -2 if an error happened.
        """
        kb_to_download = -1

        if not self.devices[self.active_device].connected:
            logging.warning(f"{self.active_device} does not seem to be connected")
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

    def send_command(self, command: DeviceCommand):
        """
        Send a command to active device.

        Refer to SifiCommands enum for possible values. All other values are reserved/unused/undefined behavior.
        """
        self.__write(f"command {command}")
        return self

    def start(self) -> dict:
        """
        Start an acquisition. Takes into account the previous configurations sent.

        Returns the "Start Time" packet.
        """
        self.send_command(DeviceCommand.START_ACQUISITION)
        while True:
            resp = self.get_data_with_key(["data", "year"])
            if resp["id"] != self.active_device:
                continue
            break
        logging.info(f"Started acquisition: {resp['data']}")
        return resp

    def stop(self):
        """
        Stop current acquisition. Does not wait for confirmation, so ensure there is enough time (~1s) for the command to reach the BLE device before destroying SifiBridge instance.
        """
        self.send_command(DeviceCommand.STOP_ACQUISITION)
        return self

    def get_data(self) -> dict:
        """
        Wait for Bridge to return a packet. Blocks until a packet is received and returns it as a dictionary.
        """

        ret = dict()
        try:
            ret = json.loads(self.bridge.stdout.readline().decode())
        except Exception as e:
            logging.error(e)
        return ret

    def get_data_with_key(self, keys: str | Iterable[str]) -> dict:
        """
        Wait for Bridge to return a packet with a specific key. Blocks until a packet is received and returns it as a dictionary.

        :param key: Key to wait for. If a string, will wait until the key is found. If an iterable, will wait until all keys are found.
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
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "ecg":
                return data

    def get_emg(self):
        """
        Get EMG data.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] in ["emg", "emgArmband"]:
                return data

    def get_eda(self):
        """
        Get EDA data.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "eda":
                return data

    def get_imu(self):
        """
        Get IMU data.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "imu":
                return data

    def get_ppg(self):
        """
        Get PPG data.
        """
        while True:
            data = self.get_data_with_key(["packet_type"])
            if data["packet_type"] == "ppg":
                return data

    def __write(self, cmd: str):
        logging.info(cmd)
        self.bridge.stdin.write((f"{cmd}\n").encode())
        self.bridge.stdin.flush()
