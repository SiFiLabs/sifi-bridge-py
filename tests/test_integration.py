"""Tier 2 integration tests: real sifibridge subprocess, no hardware.

These spawn an actual sifibridge process and exercise everything that works
without a connected device: the subprocess/queue/socket plumbing, the commands
that don't need a device (``list``, ``buffer list``, ``buffer clear --all``),
and the error paths (device commands with no device must raise
``SifiBridgeError``). This validates the real JSON wire format and response-key
unwrapping end to end.

The binary is resolved by ``sifibridge_bin.get_executable()`` — set
``SIFIBRIDGE_EXE`` to point at a dev build (e.g. ``./bin/sifibridge``). The
whole module skips if no binary is available.

Tests that need a connected device live in ``test_hardware.py`` (gated on the
``SIFI_HW`` env var) and are not run here.
"""

import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py.sifi_bridge import SifiBridgeError


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sb = sbp.SifiBridge()
        except Exception as e:  # binary missing / wrong platform
            raise unittest.SkipTest(f"sifibridge binary unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        sb = getattr(cls, "sb", None)
        if sb is not None:
            sb.close()

    def setUp(self):
        # Isolate data-queue state between tests: the shared instance carries
        # queues across tests, so drain them before each one.
        self.sb.clear_data_buffer()

    # -- subprocess / lifecycle ---------------------------------------

    def test_context_manager_closes(self):
        with sbp.SifiBridge() as sb:
            self.assertFalse(sb._closed)
        self.assertTrue(sb._closed)

    def test_close_is_idempotent(self):
        sb = sbp.SifiBridge()
        sb.close()
        sb.close()

    # -- data queues --------------------------------------------------

    def test_getters_return_empty_dict_on_timeout(self):
        self.assertEqual(self.sb.get_ecg(timeout=0.05), {})
        self.assertEqual(self.sb.get_emg(timeout=0.05), {})
        self.assertEqual(self.sb.get_eda(timeout=0.05), {})
        self.assertEqual(self.sb.get_imu(timeout=0.05), {})
        self.assertEqual(self.sb.get_ppg(timeout=0.05), {})
        self.assertEqual(self.sb.get_temperature(timeout=0.05), {})
        self.assertEqual(self.sb.get_data(timeout=0.05), {})

    def test_typed_queue_routing(self):
        for packet_type in ("ecg", "emg", "emg_armband", "imu", "ppg", "eda"):
            packet = {"packet_type": packet_type, "data": {}}
            self.sb._data_queue.put(packet)
            sensor = self.sb._PACKET_TYPE_TO_SENSOR[packet_type]
            self.sb._typed_queues[sensor].put(packet)

        # get_emg() yields both emg variants
        first = self.sb.get_emg(timeout=0.1)
        second = self.sb.get_emg(timeout=0.1)
        self.assertIn(first.get("packet_type"), ("emg", "emg_armband"))
        self.assertIn(second.get("packet_type"), ("emg", "emg_armband"))
        self.assertNotEqual(first.get("packet_type"), second.get("packet_type"))

        self.assertEqual(self.sb.get_ecg(timeout=0.1).get("packet_type"), "ecg")
        self.assertEqual(self.sb.get_imu(timeout=0.1).get("packet_type"), "imu")
        self.assertEqual(self.sb.get_ppg(timeout=0.1).get("packet_type"), "ppg")
        self.assertEqual(self.sb.get_eda(timeout=0.1).get("packet_type"), "eda")
        self.assertEqual(self.sb.get_ecg(timeout=0.05), {})

    def test_clear_data_buffer_counts_and_empties(self):
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

    # -- deviceless commands (success) --------------------------------

    def test_list_devices_returns_list(self):
        self.assertIsInstance(self.sb.list_devices(sbp.ListSources.DEVICES), list)
        self.assertIsInstance(self.sb.list_devices(sbp.ListSources.SERIAL), list)

    def test_buffer_list_returns_list(self):
        self.assertIsInstance(self.sb.buffer_list(), list)

    def test_buffer_clear_all(self):
        ret = self.sb.buffer_clear(all=True)
        self.assertIn("message", ret)

    # -- device commands without a device (errors) --------------------

    def test_info_no_device_raises(self):
        with self.assertRaises(SifiBridgeError):
            self.sb.info()

    def test_select_no_match_raises(self):
        with self.assertRaises(SifiBridgeError):
            self.sb.select_device("definitely-not-a-real-device")

    def test_send_event_without_device_raises(self):
        with self.assertRaises(SifiBridgeError):
            self.sb.send_event()

    def test_buffer_info_without_device_raises(self):
        with self.assertRaises(SifiBridgeError):
            self.sb.buffer_info()


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
