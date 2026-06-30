"""Tier 3 hardware tests: require a real, powered-on SiFi device.

These are gated on the ``SIFI_HW`` environment variable so they never run in
normal CI. Set it to a connection handle (a BLE name like ``BioPoint`` or a
MAC/UUID), or to ``1`` to auto-connect to the first device found::

    SIFI_HW=BioPoint SIFIBRIDGE_EXE=./bin/sifibridge \\
        python -m unittest tests.test_hardware -v

This is a smoke test of the full acquisition loop (connect -> configure ->
start -> receive data -> stop -> disconnect), not exhaustive coverage.
"""

import os
import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py.sifi_bridge import PacketType

_HW = os.environ.get("SIFI_HW")


@unittest.skipUnless(_HW, "set SIFI_HW=<handle|1> to run hardware tests")
class TestHardware(unittest.TestCase):
    def setUp(self):
        self.sb = sbp.SifiBridge()
        handle = None if _HW == "1" else _HW
        # Devices can take a few attempts to appear over BLE.
        connected = False
        for _ in range(20):
            if self.sb.connect(handle):
                connected = True
                break
        if not connected:
            self.sb.close()
            self.skipTest(f"could not connect to device {_HW!r}")

    def tearDown(self):
        try:
            self.sb.disconnect()
        finally:
            self.sb.close()

    def test_info_reports_active_device(self):
        info = self.sb.info()
        self.assertIn("id", info)
        self.assertEqual(self.sb.get_active_device(), info["id"])

    def test_ecg_acquisition(self):
        self.sb.configure_sensors(ecg=True)
        self.sb.configure_ecg(fs=500)
        self.sb.clear_data_buffer()
        self.assertTrue(self.sb.start())
        try:
            packet = self.sb.get_ecg(timeout=5.0)
        finally:
            self.sb.stop()
        self.assertEqual(packet.get("packet_type"), PacketType.ECG.value)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
