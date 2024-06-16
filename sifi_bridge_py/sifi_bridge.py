import subprocess as sp
from importlib import metadata  # noqa

import json
from typing import Iterable

from enum import IntEnum, Enum
from dataclasses import dataclass

import logging


class Commands(IntEnum):
    """
    Use in tandem with SifiBridge.send_command() to control Sifi device operation.
    """

    START_ACQUISITION = 0
    STOP_ACQUISITION = 1
    SET_BLE_POWER = 2
    SET_ONBOARD_FILTERING = 3
    ERASE_ONBOARD_MEMORY = 4
    DOWNLOAD_ONBOARD_MEMORY = 5
    START_STATUS_UPDATE = 6
    OPEN_LED_1 = 7
    OPEN_LED_2 = 8
    CLOSE_LED_1 = 9
    CLOSE_LED_2 = 10
    START_MOTOR = 11
    STOP_MOTOR = 12
    POWER_OFF = 13
    POWER_DEEP_SLEEP = 14
    SET_PPG_CURRENTS = 15
    SET_PPG_SENSITIVITY = 16
    SET_EMG_MAINS_NOTCH = 17
    SET_EDA_FREQUENCY = 18
    SET_EDA_GAIN = 19
    DOWNLOAD_MEMORY_SERIAL = 20
    STOP_STATUS_UPDATE = 21


class DeviceType(Enum):
    """
    Use in tandem with SifiBridge.connect() to connect to SiFi Devices via BLE name.
    """

    BIOPOINT_V1_1 = "BioPoint_v1_1"
    BIOPOINT_V1_2 = "BioPoint_v1_2"
    BIOPOINT_V1_3 = "BioPoint_v1_3"
    BIOARMBAND_LEGACY = "BioPoint_v1_1"
    BIOARMBAND = "BioArmband"  # sb >=0.6.2


@dataclass
class Device:
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

    devices: dict[Device]
    """
    SiFi Bridge devices. Used to keep track of state and cache some informations.
    """

    def __init__(self, exec_path: str = "sifi_bridge"):
        """
        Create a SiFi Bridge instance. Currently, only `stdin` and `stdout` are supported to communicate with Sifi Bridge.

        :param exec_path: Path to sifi_bridge. If executable is in PATH, you can leave it at default value.
        """
        # exe_version = (
        #     sp.run([exec_path, "-V"], stdout=sp.PIPE)
        #     .stdout.decode()
        #     .strip()
        #     .split(" ")[-1]
        # )
        # py_version = version("sifi_bridge_py")
        self.bridge = sp.Popen([exec_path], stdin=sp.PIPE, stdout=sp.PIPE)
        self.active_device = "default"
        self.devices = {self.active_device: Device(self.active_device, False, "None")}

    def create_device(self, uid: str, select: bool = True) -> bool:
        """
        Create a SiFi Bridge device named `uid` and optionally select it.

        :param uid: User-defined name of the device
        :param select: Automatically select the device after creation

        Raises a `ValueError` if `uid` contains spaces.

        Returns True if the device was created, False otherwise.
        """
        if " " in uid:
            raise ValueError(f"Spaces are not supported in UID: {uid}")

        ret = False
        self.__write(f"-n {uid}")
        if uid in self.list_devices("devices")["found_devices"]:
            self.devices[uid] = Device(uid, False, "None")
            ret = True
        if select:
            self.select_device(uid)

        return ret

    def select_device(self, uid: str) -> bool:
        """
        Select the SiFi Bridge device `uid`.

        Returns True if device was selected, False if it does not exist.
        """
        if uid in self.list_devices("devices")["found_devices"]:
            self.__write(f"-i {uid}")
            self.active_device = uid
            return True
        return False

    def list_devices(self, source: str) -> dict:
        """
        Returns all devices found from the passed `source`.

        :param source: "self" to list UID devices, "ble" to list BLE devices, "serial" for serial, and any other input will list the SiFi Bridge devices
        """
        self.__write(f"-l {source}")
        sb_devs = self.get_data_with_key("found_devices")
        return sb_devs

    def connect(self, handle: DeviceType | str) -> bool:
        """
        Try to connect to `handle`.

        :param handle: Device handle to connect to. If a string, will attempt to connect to the BLE device with that name. If a DeviceType, will attempt to connect to the BLE device as-is.

        :return: True if connection successful, False otherwise.
        """

        if isinstance(handle, DeviceType):
            handle = handle.value

        self.__write(f"-c {handle}")
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
        self.__write("-d")
        ret = self.get_data_with_key("connected")
        self.devices[self.active_device].connected = ret["connected"]
        return ret["connected"]

    def set_filters(self, enable: bool):
        """
        Set state of onboard filtering for all biochannels.
        """
        self.__write(f"-s enable_filters {int(enable)}")

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
        self.__write(f"-s ch {int(ecg)},{int(emg)},{int(eda)},{int(imu)},{int(ppg)}")

    def set_ble_power(self, power: int):
        """
        Set the BLE transmission power.

        :param power: 0 for lowest power, 1 for medium, 2 for highest
        """
        self.__write(f"-s tx_power {power}")

    def set_memory_mode(self, memory_config: int):
        """
        Configure the device's memory mode. NOTE: Onboard memory is unsupported for BioArmband.

        :param memory_config: 0 only streams data via BLE. 1 only stores data on onboard memory. 2 does both.
        """
        self.__write(f"-s mem {memory_config}")

    def configure_emg(self, bandpass_freqs: tuple = (20, 450), notch_freq: int = 50):
        """
        Configure EMG biochannel filters.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :notch_freq: Mains notch filter frequency. {50, 60} Hz, otherwise notch is disabled.
        """
        self.set_filters(True)
        self.__write(f"-s emg_cfg {bandpass_freqs[0]},{bandpass_freqs[1]},{notch_freq}")

    def configure_ecg(self, bandpass_freqs: tuple = (0, 30)):
        """
        Configure ECG biochannel filters.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        """
        self.set_filters(True)
        self.__write(f"-s ecg_cfg {bandpass_freqs[0]},{bandpass_freqs[1]}")

    def configure_eda(
        self,
        bandpass_freqs: tuple = (0, 5),
        signal_freq: int = 0,
    ):
        """
        Configure EDA biochannel.

        :param bandpass_freqs: Tuple of lower and upper cutoff frequencies for the bandpass filter.
        :signal_freq: frequency of EDA excitation signal. 0 for DC.
        """
        self.set_filters(True)
        self.__write(
            f"-s eda_cfg {bandpass_freqs[0]},{bandpass_freqs[1]},{signal_freq}"
        )

    def configure_ppg(
        self, ir: int = 9, red: int = 9, green: int = 9, blue: int = 9, sens: int = 3
    ):
        """
        Configure PPG biochannel.

        :param ir, red, green, blue: current of each PPG LED in mA (1-50)
        :param sens: sensitivity of PPG from 0 to 3, where 0 is the lowest
        """

        self.__write(f"-s ppg_cfg {ir},{red},{green},{blue},{sens}")

    def configure_sampling_freqs(self, ecg=500, emg=2000, eda=40, imu=50, ppg=50):
        """
        Configure the sampling frequencies [Hz] of biosignal acquisition. NOTE: Only available for latest BioPoint versions.
        """
        self.__write(f"-s fs_cfg {ecg},{emg},{eda},{imu},{ppg}")

    def set_data_mode(self, mode: bool):
        """
        Set the BioPoint data mode. NOTE: Only supported on the latest BioPoint versions.

        :mode: True to use Low Latency mode, in which packets are sent much faster with data from all biosignals at once. sFalse to use the conventional 1 biosignal-batch-per-packet (default)
        """

        self.__write(f"-s data_mode {1 if mode else 0}")

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
            self.send_command(Commands.START_STATUS_UPDATE)
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

        self.send_command(Commands.DOWNLOAD_ONBOARD_MEMORY)

        return kb_to_download

    def send_command(self, command: Commands):
        """
        Send a command to active device.

        Refer to SifiCommands enum for possible values. All other values are reserved/unused/undefined behavior.
        """
        self.__write(f"-cmd {int(command)}")

    def start(self) -> dict:
        """
        Start an acquisition. Takes into account the previous configurations sent.

        Returns the "Start Time" packet.
        """
        self.send_command(Commands.START_ACQUISITION)
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
        self.send_command(Commands.STOP_ACQUISITION)

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
        return self.get_data_with_key(["data", "ecg"])

    def get_emg(self):
        """
        Get EMG data.
        """
        while True:
            data = self.get_data_with_key("data")
            payload = data["data"]
            if "emg" in payload.keys() or "emg0" in payload.keys():
                return data

    def get_eda(self):
        """
        Get EDA data.
        """
        return self.get_data_with_key(["data", "eda"])

    def get_imu(self):
        """
        Get IMU data.
        """
        return self.get_data_with_key(["data", "w"])

    def get_ppg(self):
        """
        Get PPG data.
        """
        return self.get_data_with_key(["data", "r"])

    def __write(self, cmd: str):
        logging.info(cmd)
        self.bridge.stdin.write((f"{cmd}\n").encode())
        self.bridge.stdin.flush()

    def __del__(self):
        try:
            self.__write("-q")
        except Exception as e:
            logging.error(e)
