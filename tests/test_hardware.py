"""Tier 3 hardware-in-the-loop (HIL) tests: require a real, powered-on device.

These are gated on the ``SIFI_HW`` environment variable so they never run in
normal CI. Set it to a connection handle (a BLE name like ``BioPoint`` or a
MAC/UUID), or to ``1`` to auto-connect to the first device found::

    SIFI_HW=BioPoint SIFIBRIDGE_EXE=./bin/sifibridge \\
        python -m unittest tests.test_hardware -v

What is covered:

- connection lifecycle (connect / info / disconnect),
- the full acquisition loop for every sensor (configure -> start -> receive ->
  stop), validating packet type and channel shape,
- simultaneous multi-sensor acquisition,
- software events and status updates appearing in the stream,
- the buffer subsystem end to end (record -> list -> info -> pull -> export ->
  clear),
- the non-destructive actuators (LED, vibration motor).

Sensor coverage is device-dependent (e.g. PPG/EDA/temperature are BioPoint-only,
the 8-channel armband EMG is SiFiBand-only). Per-sensor tests ``skipTest`` when
no data arrives, so the same suite runs against either device without failing on
absent hardware. Destructive operations (power off, memory erase, rename) are
intentionally excluded.

**NOTE**: the LED and motor tests physically actuate the device.
"""

from __future__ import annotations

import os
import time
import tempfile
import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py import utils
from sifi_bridge_py.sifi_bridge import (
    PacketType,
    SensorChannel,
    SifiBridgeError,
)

_HW = os.environ.get("SIFI_HW")
_HANDLE = None if _HW in (None, "1") else _HW


def _channels(sensor: SensorChannel) -> tuple[str, ...]:
    """Normalize a `SensorChannel` value to a tuple of channel names."""
    value = sensor.value
    return (value,) if isinstance(value, str) else tuple(value)


def _open_connected(attempts: int = 20) -> sbp.SifiBridge | None:
    """Spawn a bridge and try to connect to the configured device.

    Devices can take a few attempts to appear over BLE, so retry. Returns a
    connected `SifiBridge`, or `None` (after cleaning up) if it never connects.
    """
    sb = sbp.SifiBridge()
    for _ in range(attempts):
        if sb.connect(_HANDLE):
            return sb
    sb.close()
    return None


@unittest.skipUnless(_HW, "set SIFI_HW=<handle|1> to run hardware tests")
class TestHardwareLifecycle(unittest.TestCase):
    """Connect / inspect / disconnect on a dedicated bridge instance."""

    def test_connect_info_disconnect(self):
        sb = _open_connected()
        if sb is None:
            self.skipTest(f"could not connect to device {_HW!r}")
        try:
            info = sb.info()
            self.assertIn("id", info)
            self.assertEqual(sb.get_active_device(), info["id"])
            # A successful disconnect leaves no active connection.
            self.assertFalse(sb.disconnect())
        finally:
            sb.close()


@unittest.skipUnless(_HW, "set SIFI_HW=<handle|1> to run hardware tests")
class TestHardwareAcquisition(unittest.TestCase):
    """Acquisition, buffering, and actuator tests over one shared connection.

    Connecting over BLE is slow, so the connection is established once for the
    whole class. ``tearDown`` stops any in-progress acquisition so a failing
    test can't leave the device streaming into the next one.
    """

    sb: sbp.SifiBridge

    @classmethod
    def setUpClass(cls):
        sb = _open_connected()
        if sb is None:
            raise unittest.SkipTest(f"could not connect to device {_HW!r}")
        cls.sb = sb

    @classmethod
    def tearDownClass(cls):
        sb = getattr(cls, "sb", None)
        if sb is not None:
            try:
                sb.disconnect()
            finally:
                sb.close()

    def tearDown(self):
        # Best-effort: ensure the device is not left acquiring between tests.
        try:
            self.sb.stop()
        except SifiBridgeError:
            pass

    # -- helpers ------------------------------------------------------

    def _only(self, **sensors):
        """Enable exactly the named sensors and disable the rest.

        Necessary because `configure_sensors` is incremental: an omitted sensor
        keeps its current state, so a test that only says ``ecg=True`` inherits
        whatever the previous test left on. With every sensor streaming at once
        the BLE link saturates and the sensor under test can go quiet, which
        looks exactly like absent hardware.
        """
        states = {s: False for s in ("ecg", "emg", "eda", "imu", "ppg")}
        states.update(sensors)
        return self.sb.configure_sensors(**states)

    def _collect(self, getter, timeout: float) -> dict:
        """Start, wait for one packet from ``getter``, then stop.

        ``skipTest`` if nothing arrives — that sensor is most likely absent on
        the connected device.
        """
        self.sb.clear_data_buffer()
        self.assertTrue(self.sb.start())
        try:
            packet = getter(timeout=timeout)
        finally:
            self.sb.stop()
        if not packet:
            self.skipTest("no data received (sensor likely absent on this device)")
        return packet

    # -- device info --------------------------------------------------

    def test_info_reports_active_device(self):
        info = self.sb.info()
        self.assertIn("id", info)
        self.assertEqual(self.sb.get_active_device(), info["id"])

    def test_info_accessors_are_populated(self):
        """The accessors must actually find their fields on a real device.

        They read out of the `configuration` block; reading them from the top
        level of `info()` returns empty without raising, which is how the first
        version of these accessors shipped looking fine.
        """
        self.assertTrue(self.sb.get_configuration())
        self.assertTrue(self.sb.get_sensors(), "sensor inventory came back empty")
        self.assertIsNotNone(self.sb.get_device_state())
        self.assertIsInstance(self.sb.get_battery(), int)

    def test_sensor_states_follow_configure_sensors(self):
        """Enabling one sensor must leave the others exactly as they were."""
        self.sb.configure_sensors(ecg=True, emg=False, eda=False, imu=False, ppg=False)
        before = self.sb.get_sensor_states()
        self.assertEqual(before.get("ecg"), True)
        self.assertEqual(before.get("emg"), False)

        self.sb.configure_sensors(emg=True)
        after = self.sb.get_sensor_states()
        self.assertEqual(after.get("emg"), True, "configure_sensors(emg=True) no-op")
        self.assertEqual(
            {s: v for s, v in after.items() if s != "emg"},
            {s: v for s, v in before.items() if s != "emg"},
            "enabling EMG also changed another sensor's state",
        )

    def test_timestamps_are_relative_to_start_time(self):
        """Sample timestamps are acquisition-relative; `start_time` anchors them.

        This is what `utils.absolute_timestamps` assumes, and the 2.0.0
        changelog describes it both ways in different entries.
        """
        self.sb.configure_sensors(ecg=True, emg=False, eda=False, imu=False, ppg=False)
        self.sb.configure_ecg(fs=500)
        self.sb.clear_data_buffer()
        self.assertTrue(self.sb.start())
        start_time, first_ecg = None, None
        deadline = time.monotonic() + 10.0
        try:
            while time.monotonic() < deadline and (start_time is None or not first_ecg):
                packet = self.sb.get_data(timeout=1.0)
                if not packet:
                    continue
                if packet.get("packet_type") == PacketType.START_TIME.value:
                    start_time = utils.get_start_time(packet)
                elif packet.get("packet_type") == PacketType.ECG.value and not first_ecg:
                    first_ecg = packet
        finally:
            self.sb.stop()

        if start_time is None or not first_ecg:
            self.skipTest("did not observe both a start_time and an ECG packet")

        timestamps = first_ecg["timestamps"]
        # Relative: the first sample of an acquisition sits at ~0, not at a
        # Unix epoch value (which would be ~1.7e9).
        self.assertLess(
            timestamps[0], 60.0, f"timestamps look absolute, not relative: {timestamps[:3]}"
        )
        self.assertTrue(
            all(b > a for a, b in zip(timestamps, timestamps[1:])),
            "timestamps are not monotonically increasing",
        )

        absolute = utils.absolute_timestamps(first_ecg, start_time)
        self.assertEqual(len(absolute), len(timestamps))
        # Anchored to the acquisition, which just happened.
        self.assertAlmostEqual(absolute[0], start_time + timestamps[0], places=6)
        self.assertLess(abs(absolute[0] - time.time()), 300.0)

    # -- per-sensor acquisition ---------------------------------------

    def test_ecg_acquisition(self):
        self._only(ecg=True)
        self.sb.configure_ecg(fs=500)
        packet = self._collect(self.sb.get_ecg, 8.0)
        self.assertEqual(packet["packet_type"], PacketType.ECG.value)
        self.assertIn(SensorChannel.ECG.value, packet["data"])

    def test_emg_acquisition(self):
        self._only(emg=True)
        self.sb.configure_emg(fs=2000)
        packet = self._collect(self.sb.get_emg, 8.0)
        # BioPoint reports `emg`, SiFiBand reports `emg_armband`.
        self.assertIn(
            packet["packet_type"],
            (PacketType.EMG.value, PacketType.EMG_ARMBAND.value),
        )
        self.assertTrue(packet["data"])

    def test_eda_acquisition(self):
        self._only(eda=True)
        self.sb.configure_eda(fs=50)
        packet = self._collect(self.sb.get_eda, 8.0)
        self.assertEqual(packet["packet_type"], PacketType.EDA.value)
        self.assertIn(SensorChannel.EDA.value, packet["data"])

    def test_imu_acquisition(self):
        self._only(imu=True)
        self.sb.configure_imu(fs=100)
        packet = self._collect(self.sb.get_imu, 8.0)
        self.assertEqual(packet["packet_type"], PacketType.IMU.value)
        for channel in _channels(SensorChannel.IMU):
            self.assertIn(channel, packet["data"])

    def test_ppg_acquisition(self):
        self._only(ppg=True)
        self.sb.configure_ppg(sps=100, avg=1)
        packet = self._collect(self.sb.get_ppg, 8.0)
        self.assertEqual(packet["packet_type"], PacketType.PPG.value)
        self.assertTrue(
            any(ch in packet["data"] for ch in _channels(SensorChannel.PPG))
        )

    def test_temperature_acquisition(self):
        # Temperature is not part of `configure sensors` and has no enable of
        # its own, but the device only emits it alongside an otherwise active
        # acquisition: with all five biosensors off, nothing arrives. So pair
        # it with ECG rather than running it alone.
        self._only(ecg=True)
        self.sb.configure_temperature(fs=1.0)
        # Temperature streams at ~1 Hz, so allow extra time for the first packet.
        packet = self._collect(self.sb.get_temperature, 15.0)
        self.assertEqual(packet["packet_type"], PacketType.TEMPERATURE.value)
        self.assertIn(SensorChannel.TEMPERATURE.value, packet["data"])

    def test_multi_sensor_acquisition(self):
        self._only(ecg=True, imu=True)
        self.sb.configure_ecg(fs=500)
        self.sb.configure_imu(fs=100)
        self.sb.clear_data_buffer()
        self.assertTrue(self.sb.start())
        try:
            ecg = self.sb.get_ecg(timeout=8.0)
            imu = self.sb.get_imu(timeout=8.0)
        finally:
            self.sb.stop()
        if not ecg or not imu:
            self.skipTest("did not receive both ECG and IMU on this device")
        self.assertEqual(ecg["packet_type"], PacketType.ECG.value)
        self.assertEqual(imu["packet_type"], PacketType.IMU.value)

    # -- events & status ----------------------------------------------

    def test_event_appears_in_stream(self):
        self._only(ecg=True)
        self.sb.configure_ecg(fs=500)
        self.sb.clear_data_buffer()
        self.assertTrue(self.sb.start())
        try:
            self.sb.get_ecg(timeout=8.0)  # ensure the stream is flowing
            self.sb.send_event()
            event = self.sb.get_event(timeout=8.0)
        finally:
            self.sb.stop()
        if not event:
            self.skipTest("no event packet received")
        self.assertEqual(event["packet_type"], PacketType.EVENT.value)

    def test_status_updates(self):
        self.sb.clear_data_buffer()
        self.sb.set_status_updates(True)
        try:
            status = {}
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                packet = self.sb.get_data(timeout=2.0)
                if packet.get("packet_type") == PacketType.STATUS.value:
                    status = packet
                    break
        finally:
            self.sb.set_status_updates(False)
        if not status:
            self.skipTest("no status packet received")
        self.assertEqual(status["packet_type"], PacketType.STATUS.value)

    # -- buffer subsystem, end to end ---------------------------------

    def test_buffer_end_to_end(self):
        self._only(ecg=True)
        self.sb.configure_ecg(fs=500)
        self.sb.buffer_clear(all=True)

        self.assertTrue(self.sb.start())
        time.sleep(2.0)  # let sifibridge buffer a couple seconds of data
        self.sb.stop()

        acquisitions = self.sb.buffer_list()
        if not acquisitions:
            self.skipTest("no acquisition was buffered (no ECG data on this device)")

        acq_id = acquisitions[-1]["id"]

        info = self.sb.buffer_info(acquisition_id=acq_id)
        self.assertIn("acquisition", info)

        pulled = self.sb.buffer_pull("ecg", acquisition_id=acq_id)
        self.assertTrue(pulled)
        self.assertEqual(pulled[0]["sensor"], "ecg")

        with tempfile.TemporaryDirectory() as out_dir:
            self.sb.buffer_export(fmt="csv", output_dir=out_dir)
            self.assertTrue(os.listdir(out_dir), "expected exported file(s)")

        cleared = self.sb.buffer_clear(all=True)
        self.assertIn("message", cleared)
        self.assertEqual(self.sb.buffer_list(), [])

    # -- actuators (physical) -----------------------------------------

    def test_led_toggle(self):
        self.assertIsInstance(self.sb.set_led(1, True), dict)
        self.assertIsInstance(self.sb.set_led(1, False), dict)

    def test_motor(self):
        self.sb.set_motor(True)
        self.sb.set_motor(False)

    def test_motor_intensity(self):
        self.assertIsInstance(self.sb.set_motor_intensity(5), dict)


def _flatten(obj, prefix=()):
    """Yield ``(path, leaf)`` pairs for a nested mapping.

    Lets two `info()["configuration"]` snapshots be compared without the test
    knowing how sifibridge nests the configuration block.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            for item in _flatten(value, prefix + (str(key),)):
                yield item
    else:
        yield prefix, obj


def _changed_paths(before: dict, after: dict) -> set:
    """The set of paths whose value differs between two configurations."""
    flat_before = dict(_flatten(before))
    flat_after = dict(_flatten(after))
    return {
        path
        for path in set(flat_before) | set(flat_after)
        if flat_before.get(path) != flat_after.get(path)
    }


@unittest.skipUnless(_HW, "set SIFI_HW=<handle|1> to run hardware tests")
class TestPartialConfigurationPreservesSettings(unittest.TestCase):
    """The round-trip proof that a partial `configure_*` leaves the rest alone.

    Every configuration parameter defaults to None, meaning "leave as-is", and
    the wrapper omits the flag entirely. `tests/test_unit.py` proves the flag
    is omitted; only a real device proves the setting actually survives. This
    reads the device's configuration, changes exactly one parameter, reads it
    back, and asserts nothing else moved.
    """

    @classmethod
    def setUpClass(cls):
        sb = _open_connected()
        if sb is None:
            raise unittest.SkipTest(f"could not connect to device {_HW!r}")
        cls.sb = sb

    @classmethod
    def tearDownClass(cls):
        sb = getattr(cls, "sb", None)
        if sb is not None:
            try:
                sb.disconnect()
            finally:
                sb.close()

    def _configuration(self) -> dict:
        config = self.sb.get_configuration()
        if not config:
            self.skipTest(
                "device info() has no 'configuration' block (keys: "
                f"{sorted(self.sb.info())}); firmware may predate sifibridge 2.0.0"
            )
        return config

    def _assert_only_changed(self, apply, field: str):
        """Apply a one-parameter change; assert only `field` moved."""
        before = self._configuration()
        apply()
        after = self._configuration()

        changed = _changed_paths(before, after)
        self.assertTrue(
            changed,
            f"configuring {field} changed nothing — the test cannot tell "
            "preservation from a no-op, pick a different value",
        )
        unexpected = {path for path in changed if path[-1] != field}
        self.assertEqual(
            unexpected,
            set(),
            f"configuring only {field} also changed {sorted(unexpected)}; "
            "an omitted flag is supposed to leave its setting untouched",
        )

    def test_emg_fs_only_changes_fs(self):
        # Put the device in a known state, then move one parameter off it.
        self.sb.configure_emg(
            fs=2000,
            dc_notch=True,
            mains_notch=50,
            bandpass=True,
            flo=20,
            fhi=450,
        )
        self._assert_only_changed(lambda: self.sb.configure_emg(fs=1000), "fs")

    def test_ecg_mains_notch_only_changes_mains_notch(self):
        self.sb.configure_ecg(
            fs=500,
            dc_notch=True,
            mains_notch=50,
            bandpass=True,
            flo=0,
            fhi=30,
        )
        before = self._configuration()
        self.sb.configure_ecg(mains_notch=60)
        after = self._configuration()
        changed = _changed_paths(before, after)
        self.assertTrue(changed, "switching the mains notch to 60 Hz changed nothing")
        # The field name for the notch is firmware-defined; assert only that
        # every changed path sits under ECG and mentions the notch.
        for path in changed:
            self.assertTrue(
                any("ecg" in part.lower() for part in path)
                and any("notch" in part.lower() for part in path),
                f"configuring only the ECG mains notch also changed {path}",
            )

    def test_enabling_one_sensor_leaves_the_others(self):
        self.sb.configure_sensors(ecg=False, emg=True, eda=False, imu=False, ppg=False)
        before = self._configuration()
        self.sb.configure_sensors(imu=True)
        after = self._configuration()
        changed = _changed_paths(before, after)
        self.assertTrue(changed, "enabling the IMU changed nothing")
        for path in changed:
            self.assertTrue(
                any("imu" in part.lower() for part in path),
                f"enabling only the IMU also changed {path}",
            )


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
