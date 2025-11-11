import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py.sifi_bridge import (
    BleTxPower,
    MemoryMode,
    PpgSensitivity,
)


class TestSifiBridge(unittest.TestCase):
    sb = sbp.SifiBridge()

    def test_show(self):
        assert "connected" in self.sb.show().keys()

    def test_configure_ecg(self):
        self.sb.configure_ecg((1, 35))

    def test_configure_emg(self):
        self.sb.configure_emg((1, 35), None)
        self.sb.configure_emg((1, 35), 50)
        self.sb.configure_emg((1, 35), 60)

    def test_configure_eda(self):
        self.sb.configure_eda((1, 6), 0)
        self.sb.configure_eda((1, 6), 15)
        self.sb.configure_eda((1, 6), 50000)

    def test_configure_ppg(self):
        self.sb.configure_ppg(8, 8, 8, 8, PpgSensitivity.LOW)
        self.sb.configure_ppg(8, 8, 8, 8, PpgSensitivity.MEDIUM)
        self.sb.configure_ppg(8, 8, 8, 8, PpgSensitivity.HIGH)
        self.sb.configure_ppg(8, 8, 8, 8, PpgSensitivity.MAX)

    def test_configure_sampling_frequencies(self):
        self.sb.configure_sampling_freqs(500, 2000, 100, 100, 100)

    def test_set_ble_power(self):
        self.sb.set_ble_power(BleTxPower.LOW)
        self.sb.set_ble_power(BleTxPower.MEDIUM)
        self.sb.set_ble_power(BleTxPower.HIGH)

    def test_set_memory_mode(self):
        self.sb.set_memory_mode(MemoryMode.DEVICE)
        self.sb.set_memory_mode(MemoryMode.STREAMING)
        self.sb.set_memory_mode(MemoryMode.BOTH)

    def test_set_channels(self):
        self.sb.configure_sensors(False, False, False, False, False)
        self.sb.configure_sensors(True, True, True, True, True)

    def test_set_filters(self):
        self.sb.set_filters(True)
        self.sb.set_filters(False)

    def test_set_low_latency_mode(self):
        self.sb.set_low_latency_mode(True)
        self.sb.set_low_latency_mode(False)

    def test_list_devices(self):
        # self.sb.list_devices(sbp.ListSources.BLE) # could fail in runner?
        self.sb.list_devices(sbp.ListSources.DEVICES)
        self.sb.list_devices(sbp.ListSources.SERIAL)

    def test_select_device(self):
        devs = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.sb.select_device(devs[0])
        assert self.sb.active_device == devs[0]

    def test_create_device_no_select(self):
        test_device_name = "create_device_no_select"
        self.sb.select_device("device-1")
        active_device = self.sb.active_device
        self.sb.create_device(test_device_name, False)
        assert self.sb.active_device == active_device

    def test_create_device_with_select(self):
        test_device_name = "create_device_with_select"
        self.sb.select_device("device-1")
        self.sb.create_device(test_device_name, True)
        assert self.sb.active_device == test_device_name

    def test_delete_device(self):
        test_device_name = "delete_device"
        self.sb.create_device(test_device_name, True)
        assert test_device_name in self.sb.list_devices(sbp.ListSources.DEVICES)
        self.sb.delete_device(test_device_name)
        assert test_device_name not in self.sb.list_devices(sbp.ListSources.DEVICES)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    unittest.main()
