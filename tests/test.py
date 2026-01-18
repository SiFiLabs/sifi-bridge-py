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
        """Test that show() returns device information."""
        result = self.sb.show()
        self.assertIn("connected", result.keys())

    def test_configure_ecg(self):
        """Test ECG configuration with new 2.0 API."""
        # Test with default parameters
        self.sb.configure_ecg()

        # Test with custom bandpass filter
        self.sb.configure_ecg(flo=1, fhi=35, bandpass=True)

        # Test with different mains notch frequencies
        self.sb.configure_ecg(mains_notch=None)
        self.sb.configure_ecg(mains_notch=50)
        self.sb.configure_ecg(mains_notch=60)

    def test_configure_emg(self):
        """Test EMG configuration with new 2.0 API."""
        # Test with default parameters
        self.sb.configure_emg()

        # Test with custom bandpass filter
        self.sb.configure_emg(flo=20, fhi=450, bandpass=True)

        # Test with different mains notch frequencies
        self.sb.configure_emg(mains_notch=None)
        self.sb.configure_emg(mains_notch=50)
        self.sb.configure_emg(mains_notch=60)

    def test_configure_eda(self):
        """Test EDA configuration with new 2.0 API."""
        # Test with default parameters
        self.sb.configure_eda()

        # Test with custom bandpass filter and frequencies
        self.sb.configure_eda(flo=0, fhi=5, freq=0)
        self.sb.configure_eda(flo=1, fhi=6, freq=15)
        self.sb.configure_eda(flo=0, fhi=10, freq=50000)

    def test_configure_ppg(self):
        """Test PPG configuration with new 2.0 API."""
        # Test with different sensitivity levels using keyword arguments
        self.sb.configure_ppg(ir=8, red=8, green=8, blue=8, sens=PpgSensitivity.LOW)
        self.sb.configure_ppg(ir=8, red=8, green=8, blue=8, sens=PpgSensitivity.MEDIUM)
        self.sb.configure_ppg(ir=8, red=8, green=8, blue=8, sens=PpgSensitivity.HIGH)
        self.sb.configure_ppg(ir=8, red=8, green=8, blue=8, sens=PpgSensitivity.MAX)

    def test_configure_sampling_frequencies(self):
        """Test sampling frequency configuration."""
        self.sb.configure_sampling_freqs(500, 2000, 100, 100, 100)
        self.sb.configure_sampling_freqs(ecg=250, emg=1000, eda=50, imu=50, ppg=50)

    def test_set_ble_power(self):
        """Test BLE transmission power configuration."""
        self.sb.set_ble_power(BleTxPower.LOW)
        self.sb.set_ble_power(BleTxPower.MEDIUM)
        self.sb.set_ble_power(BleTxPower.HIGH)

    def test_set_memory_mode(self):
        """Test memory mode configuration."""
        self.sb.set_memory_mode(MemoryMode.DEVICE)
        self.sb.set_memory_mode(MemoryMode.STREAMING)
        self.sb.set_memory_mode(MemoryMode.BOTH)

    def test_configure_sensors(self):
        """Test sensor enable/disable configuration."""
        # Disable all sensors
        self.sb.configure_sensors(ecg=False, emg=False, eda=False, imu=False, ppg=False)

        # Enable all sensors
        self.sb.configure_sensors(ecg=True, emg=True, eda=True, imu=True, ppg=True)

    def test_set_filters(self):
        """Test onboard filtering configuration."""
        self.sb.set_filters(True)
        self.sb.set_filters(False)

    def test_set_low_latency_mode(self):
        """Test low latency mode configuration."""
        self.sb.set_low_latency_mode(True)
        self.sb.set_low_latency_mode(False)

    def test_clear_data_buffer(self):
        """Test the new clear_data_buffer() function."""
        # Clear buffer should return number of packets discarded
        discarded = self.sb.clear_data_buffer()
        self.assertIsInstance(discarded, int)
        self.assertGreaterEqual(discarded, 0)

    def test_list_devices(self):
        """Test device listing from different sources."""
        # BLE could fail in CI/CD runner, so commented out
        # self.sb.list_devices(sbp.ListSources.BLE)

        devices_list = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.assertIsInstance(devices_list, list)

        serial_list = self.sb.list_devices(sbp.ListSources.SERIAL)
        self.assertIsInstance(serial_list, list)

    def test_select_device(self):
        """Test device selection."""
        devs = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.assertGreater(len(devs), 0, "No devices available to select")

        self.sb.select_device(devs[0])
        self.assertEqual(self.sb.active_device, devs[0])

    def test_send_event(self):
        """Test event generation."""
        ret = self.sb.send_event()
        assert "event" in ret.keys()

    def test_create_device_no_select(self):
        """Test device creation without selecting it."""
        test_device_name = "create_device_no_select"

        # Select a known device first
        self.sb.select_device("device")
        active_device = self.sb.active_device

        # Create new device without selecting it
        self.sb.create_device(test_device_name, select=False)

        # Active device should not change
        self.assertEqual(self.sb.active_device, active_device)

    def test_create_device_with_select(self):
        """Test device creation with automatic selection."""
        test_device_name = "create_device_with_select"

        # Select a known device first
        self.sb.select_device("device")

        # Create new device and select it
        self.sb.create_device(test_device_name, select=True)

        # Active device should be the newly created one
        self.assertEqual(self.sb.active_device, test_device_name)

    def test_delete_device(self):
        """Test device deletion."""
        test_device_name = "delete_device"

        # Create the device
        self.sb.create_device(test_device_name, select=True)

        # Verify it exists
        devices = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.assertIn(test_device_name, devices)

        # Delete it
        self.sb.delete_device(test_device_name)

        # Verify it's gone
        devices = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.assertNotIn(test_device_name, devices)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    unittest.main()
