import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py.sifi_bridge import (
    BleTxPower,
    MemoryMode,
    PpgSensitivity,
)


class TestSifiBridge(unittest.TestCase):
    sb = sbp.SifiBridge()

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def test_close_is_idempotent(self):
        """close() can be called multiple times without raising."""
        sb = sbp.SifiBridge()
        sb.close()
        sb.close()

    def test_context_manager(self):
        """SifiBridge can be used as a context manager and closes on exit."""
        with sbp.SifiBridge() as sb:
            self.assertFalse(sb._closed)
        self.assertTrue(sb._closed)

    def test_getters_return_empty_dict_on_timeout(self):
        """Per-sensor getters return {} when no data arrives within timeout."""
        self.sb.clear_data_buffer()
        self.assertEqual(self.sb.get_ecg(timeout=0.05), {})
        self.assertEqual(self.sb.get_emg(timeout=0.05), {})
        self.assertEqual(self.sb.get_eda(timeout=0.05), {})
        self.assertEqual(self.sb.get_imu(timeout=0.05), {})
        self.assertEqual(self.sb.get_ppg(timeout=0.05), {})
        self.assertEqual(self.sb.get_temperature(timeout=0.05), {})
        self.assertEqual(self.sb.get_data(timeout=0.05), {})

    def test_typed_queue_routing(self):
        """Each sensor getter only returns packets of its own type, and
        get_data() should drain interleaved packets in order."""
        self.sb.clear_data_buffer()
        # Inject packets directly as if the data worker had received them
        for packet_type in ("ecg", "emg", "emg_armband", "imu", "ppg", "eda"):
            packet = {"packet_type": packet_type, "data": {}}
            self.sb._data_queue.put(packet)
            sensor = self.sb._PACKET_TYPE_TO_SENSOR[packet_type]
            self.sb._typed_queues[sensor].put(packet)

        # get_emg() should yield both emg variants
        first_emg = self.sb.get_emg(timeout=0.1)
        second_emg = self.sb.get_emg(timeout=0.1)
        self.assertIn(first_emg.get("packet_type"), ("emg", "emg_armband"))
        self.assertIn(second_emg.get("packet_type"), ("emg", "emg_armband"))
        self.assertNotEqual(first_emg.get("packet_type"), second_emg.get("packet_type"))

        # Other getters return their own type
        self.assertEqual(self.sb.get_ecg(timeout=0.1).get("packet_type"), "ecg")
        self.assertEqual(self.sb.get_imu(timeout=0.1).get("packet_type"), "imu")
        self.assertEqual(self.sb.get_ppg(timeout=0.1).get("packet_type"), "ppg")
        self.assertEqual(self.sb.get_eda(timeout=0.1).get("packet_type"), "eda")

        # And typed queues are now empty
        self.assertEqual(self.sb.get_ecg(timeout=0.05), {})

    def test_clear_data_buffer_clears_typed_queues(self):
        """clear_data_buffer empties both the generic queue and per-sensor queues."""
        for packet_type in ("ecg", "emg", "imu"):
            packet = {"packet_type": packet_type, "data": {}}
            self.sb._data_queue.put(packet)
            self.sb._typed_queues[self.sb._PACKET_TYPE_TO_SENSOR[packet_type]].put(
                packet
            )
        discarded = self.sb.clear_data_buffer()
        self.assertEqual(discarded, 3)  # generic-queue count
        for q in self.sb._typed_queues.values():
            self.assertTrue(q.empty())

    def test_show_no_device_raises(self):
        """Test that show() raises when there is no active device (no `new`/default in 2.0.0)."""
        from sifi_bridge_py.sifi_bridge import SifiBridgeError

        with self.assertRaises(SifiBridgeError):
            self.sb.show()

    def test_configure_ecg(self):
        """Test ECG configuration with value verification."""
        ret = self.sb.configure_ecg(
            state=True,
            fs=500,
            dc_notch=True,
            mains_notch=50,
            bandpass=True,
            flo=0,
            fhi=30,
        )
        ecg = ret["config"]["ecg"]
        self.assertEqual(ecg["state"], "on")
        self.assertEqual(ecg["fs"], 500.0)
        self.assertEqual(ecg["dc_notch"], "on")
        self.assertEqual(ecg["mains_notch"], 50)
        self.assertEqual(ecg["bandpass"], "on")
        self.assertEqual(ecg["flo"], 0)
        self.assertEqual(ecg["fhi"], 30)

        # Custom bandpass
        ret = self.sb.configure_ecg(flo=1, fhi=35, bandpass=True)
        ecg = ret["config"]["ecg"]
        self.assertEqual(ecg["flo"], 1)
        self.assertEqual(ecg["fhi"], 35)
        self.assertEqual(ecg["bandpass"], "on")

        # Mains notch variants
        ret = self.sb.configure_ecg(mains_notch=None)
        self.assertEqual(ret["config"]["ecg"]["mains_notch"], 0)

        ret = self.sb.configure_ecg(mains_notch=60)
        self.assertEqual(ret["config"]["ecg"]["mains_notch"], 60)

    def test_configure_emg(self):
        """Test EMG configuration with value verification."""
        ret = self.sb.configure_emg(
            state=True,
            fs=2000,
            dc_notch=True,
            mains_notch=50,
            bandpass=True,
            flo=20,
            fhi=450,
        )
        emg = ret["config"]["emg"]
        self.assertEqual(emg["state"], "on")
        self.assertEqual(emg["fs"], 2000.0)
        self.assertEqual(emg["dc_notch"], "on")
        self.assertEqual(emg["mains_notch"], 50)
        self.assertEqual(emg["bandpass"], "on")
        self.assertEqual(emg["flo"], 20)
        self.assertEqual(emg["fhi"], 450)

        # Mains notch variants
        ret = self.sb.configure_emg(mains_notch=None)
        self.assertEqual(ret["config"]["emg"]["mains_notch"], 0)

        ret = self.sb.configure_emg(mains_notch=60)
        self.assertEqual(ret["config"]["emg"]["mains_notch"], 60)

    def test_configure_eda(self):
        """Test EDA configuration with value verification."""
        ret = self.sb.configure_eda(
            state=True,
            fs=50,
            dc_notch=True,
            mains_notch=50,
            bandpass=True,
            flo=0,
            fhi=5,
            freq=0,
        )
        eda = ret["config"]["eda"]
        self.assertEqual(eda["state"], "on")
        self.assertEqual(eda["fs"], 50.0)
        self.assertEqual(eda["dc_notch"], "on")
        self.assertEqual(eda["mains_notch"], 50)
        self.assertEqual(eda["bandpass"], "on")
        self.assertEqual(eda["flo"], 0)
        self.assertEqual(eda["fhi"], 5)
        self.assertEqual(eda["freq"], 0)

        # Different freq values
        ret = self.sb.configure_eda(freq=15)
        self.assertEqual(ret["config"]["eda"]["freq"], 15)

        ret = self.sb.configure_eda(freq=50000)
        self.assertEqual(ret["config"]["eda"]["freq"], 50000)

    def test_configure_ppg(self):
        """Test PPG configuration with value verification."""
        ret = self.sb.configure_ppg(
            state=True,
            fs=100,
            ir=8,
            red=10,
            green=12,
            blue=14,
            sens=PpgSensitivity.LOW,
            avg=4,
        )
        ppg = ret["config"]["ppg"]
        self.assertEqual(ppg["state"], "on")
        self.assertEqual(ppg["fs"], 100.0)
        self.assertEqual(ppg["iir"], 8)
        self.assertEqual(ppg["ired"], 10)
        self.assertEqual(ppg["igreen"], 12)
        self.assertEqual(ppg["iblue"], 14)
        self.assertEqual(ppg["sens"], "low")
        self.assertEqual(ppg["avg"], 4)

        # All sensitivity levels
        for sens in PpgSensitivity:
            ret = self.sb.configure_ppg(sens=sens)
            self.assertEqual(ret["config"]["ppg"]["sens"], sens.value)

    def test_configure_imu(self):
        """Test IMU configuration with value verification."""
        ret = self.sb.configure_imu(state=True, fs=200, accel_range=4, gyro_range=1000)
        imu = ret["config"]["imu"]
        self.assertEqual(imu["state"], "on")
        self.assertEqual(imu["fs"], 200.0)
        self.assertEqual(imu["acc_range"], 4)
        self.assertEqual(imu["gyro_range"], 1000)

        # Different ranges
        ret = self.sb.configure_imu(accel_range=16, gyro_range=2000)
        imu = ret["config"]["imu"]
        self.assertEqual(imu["acc_range"], 16)
        self.assertEqual(imu["gyro_range"], 2000)

    def test_configure_sampling_frequencies(self):
        """Test sampling frequency configuration."""
        self.sb.configure_sampling_freqs(500, 2000, 100, 100, 100)
        self.sb.configure_sampling_freqs(ecg=250, emg=1000, eda=50, imu=50, ppg=50)

    def test_set_ble_power(self):
        """Test BLE transmission power configuration with value verification."""
        for power in BleTxPower:
            ret = self.sb.set_ble_power(power)
            self.assertEqual(ret["config"]["ble_power"]["power"], power.value)

    def test_set_memory_mode(self):
        """Test memory mode configuration with value verification."""
        for mode in MemoryMode:
            ret = self.sb.set_memory_mode(mode)
            self.assertEqual(ret["config"]["memory"]["mode"], mode.value)

    def test_configure_sensors(self):
        """Test sensor enable/disable configuration with value verification."""
        # Disable all sensors
        ret = self.sb.configure_sensors(
            ecg=False,
            emg=False,
            eda=False,
            imu=False,
            ppg=False,
        )
        sensors = ret["config"]["sensors"]
        self.assertEqual(sensors["ecg"], "off")
        self.assertEqual(sensors["emg"], "off")
        self.assertEqual(sensors["eda"], "off")
        self.assertEqual(sensors["imu"], "off")
        self.assertEqual(sensors["ppg"], "off")

        # Enable all sensors
        ret = self.sb.configure_sensors(
            ecg=True,
            emg=True,
            eda=True,
            imu=True,
            ppg=True,
        )
        sensors = ret["config"]["sensors"]
        self.assertEqual(sensors["ecg"], "on")
        self.assertEqual(sensors["emg"], "on")
        self.assertEqual(sensors["eda"], "on")
        self.assertEqual(sensors["imu"], "on")
        self.assertEqual(sensors["ppg"], "on")

    def test_set_filters(self):
        """Test onboard filtering configuration with value verification."""
        ret = self.sb.set_filters(True)
        self.assertEqual(ret["config"]["filtering"]["state"], "on")

        ret = self.sb.set_filters(False)
        self.assertEqual(ret["config"]["filtering"]["state"], "off")

    def test_set_low_latency_mode(self):
        """Test low latency mode configuration with value verification."""
        ret = self.sb.set_low_latency_mode(True)
        self.assertEqual(ret["config"]["low_latency_mode"]["state"], "on")

        ret = self.sb.set_low_latency_mode(False)
        self.assertEqual(ret["config"]["low_latency_mode"]["state"], "off")

    def test_set_night_mode(self):
        """Test stealth/night mode configuration with value verification."""
        ret = self.sb.set_night_mode(True)
        self.assertEqual(ret["config"]["night_mode"]["state"], "on")

        ret = self.sb.set_night_mode(False)
        self.assertEqual(ret["config"]["night_mode"]["state"], "off")

    def test_set_motor_intensity(self):
        """Test motor intensity configuration with value verification."""
        ret = self.sb.set_motor_intensity(5)
        self.assertEqual(ret["config"]["motor_intensity"]["level"], 5)

        ret = self.sb.set_motor_intensity(10)
        self.assertEqual(ret["config"]["motor_intensity"]["level"], 10)

        # Boundary validation
        with self.assertRaises(ValueError):
            self.sb.set_motor_intensity(0)
        with self.assertRaises(ValueError):
            self.sb.set_motor_intensity(11)

    def test_clear_data_buffer(self):
        """Test the clear_data_buffer() function."""
        discarded = self.sb.clear_data_buffer()
        self.assertIsInstance(discarded, int)
        self.assertGreaterEqual(discarded, 0)

    def test_list_devices(self):
        """Test device listing from different sources."""
        devices_list = self.sb.list_devices(sbp.ListSources.DEVICES)
        self.assertIsInstance(devices_list, list)

        serial_list = self.sb.list_devices(sbp.ListSources.SERIAL)
        self.assertIsInstance(serial_list, list)

    def test_select_no_match_raises(self):
        """select on a non-existent name should surface an `error` response as SifiBridgeError."""
        from sifi_bridge_py.sifi_bridge import SifiBridgeError

        with self.assertRaises(SifiBridgeError):
            self.sb.select_device("definitely-not-a-real-device")

    def test_send_event_without_device_raises(self):
        """event without a connected device should surface an `error` response."""
        from sifi_bridge_py.sifi_bridge import SifiBridgeError

        with self.assertRaises(SifiBridgeError):
            self.sb.send_event()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    unittest.main()
