"""Tier 1 unit tests: pure wrapper logic, no subprocess and no hardware.

Every public command funnels through ``SifiBridge._request``. These tests
replace that one method with a recorder, which lets us assert two things
without spawning sifibridge or owning a device:

1. the exact REPL command line the wrapper sends (catches wrong flags, e.g.
   ``--device`` vs ``--handle`` or ``--iir`` vs ``--led-ir``), and
2. that the response key is unwrapped correctly (e.g. ``["connect"]["connected"]``).

Command lines are compared token-wise (``str.split()``) so incidental
whitespace is ignored while flags and values still must match exactly.
"""

import unittest

from sifi_bridge_py.sifi_bridge import (
    SifiBridge,
    SifiBridgeError,
    SifiBridgeTimeout,
    BleTxPower,
    MemoryMode,
    PpgSensitivity,
    ListSources,
)


class _AutoDict(dict):
    """A dict that auto-vivifies missing keys.

    Lets response unwrapping like ``resp["connect"]["connected"]`` succeed even
    when a test only cares about the command line and supplies no canned
    response.
    """

    def __missing__(self, key):
        self[key] = _AutoDict()
        return self[key]


class FakeRequest:
    """Stand-in for ``SifiBridge._request``.

    Records every command line and returns a canned response. ``response`` may
    be a dict (returned verbatim), a callable ``line -> dict`` (for multi-call
    methods or to raise), or ``None`` (returns an auto-vivifying dict).
    """

    def __init__(self, response=None):
        self.calls = []
        self.timeouts = []
        self._response = response

    def __call__(self, line, timeout=None):
        self.calls.append(line)
        self.timeouts.append(timeout)
        if self._response is None:
            return _AutoDict()
        if callable(self._response):
            return self._response(line)
        return self._response

    @property
    def last(self):
        return self.calls[-1]


def make_sb(response=None) -> tuple[SifiBridge, "FakeRequest"]:
    """A SifiBridge with no subprocess: __init__ is skipped and ``_request`` is
    replaced by a recorder."""
    sb = SifiBridge.__new__(SifiBridge)
    rec = FakeRequest(response)
    sb._request = rec
    return sb, rec


def tok(line: str):
    return line.split()


class TestConfigureCommands(unittest.TestCase):
    def test_configure_sensors(self):
        sb, req = make_sb()
        sb.configure_sensors(ecg=True, imu=True)
        self.assertEqual(
            tok(req.last),
            tok("configure sensors --ecg on --emg off --eda off --imu on --ppg off"),
        )

    def test_configure_ecg_default(self):
        sb, req = make_sb()
        sb.configure_ecg()
        self.assertEqual(
            tok(req.last),
            tok(
                "configure ecg --fs 500 --dc-notch on --mains-notch 50 "
                "--bandpass on --bandpass-low 0 --bandpass-high 30"
            ),
        )

    def test_configure_ecg_mains_notch_variants(self):
        sb, req = make_sb()
        sb.configure_ecg(mains_notch=None)
        self.assertIn("--mains-notch off", req.last)
        sb.configure_ecg(mains_notch=50)
        self.assertIn("--mains-notch 50", req.last)
        sb.configure_ecg(mains_notch=60)
        self.assertIn("--mains-notch 60", req.last)

    def test_configure_emg(self):
        sb, req = make_sb()
        sb.configure_emg(fs=1000, flo=20, fhi=450)
        self.assertEqual(
            tok(req.last),
            tok(
                "configure emg --fs 1000 --dc-notch on --mains-notch 50 "
                "--bandpass on --bandpass-low 20 --bandpass-high 450"
            ),
        )

    def test_configure_eda_includes_freq(self):
        sb, req = make_sb()
        sb.configure_eda(freq=15)
        self.assertEqual(tok(req.last)[:2], ["configure", "eda"])
        self.assertIn("--freq 15", req.last)

    def test_configure_ppg_default_uses_led_flags(self):
        sb, req = make_sb()
        sb.configure_ppg()
        self.assertEqual(
            tok(req.last),
            tok(
                "configure ppg --sps 100 --led-ir 9 --led-red 9 --led-green 9 "
                "--led-blue 9 --sens medium --avg 1"
            ),
        )

    def test_configure_ppg_sens_accepts_str_and_enum(self):
        sb, req = make_sb()
        sb.configure_ppg(sens="high")
        self.assertIn("--sens high", req.last)
        sb.configure_ppg(sens=PpgSensitivity.MAX)
        self.assertIn("--sens max", req.last)

    def test_configure_imu(self):
        sb, req = make_sb()
        sb.configure_imu(fs=200, accel_range=4, gyro_range=1000)
        self.assertEqual(
            tok(req.last),
            tok("configure imu --fs 200 --acc-range 4 --gyro-range 1000"),
        )

    def test_configure_temperature(self):
        sb, req = make_sb()
        sb.configure_temperature(fs=2)
        self.assertEqual(tok(req.last), tok("configure temperature --fs 2"))

    def test_configure_temperature_float_fs_has_no_trailing_zero(self):
        # The binary only accepts "0.1", "1", "2", "10" — a float default of
        # 1.0 must render as "1", not "1.0", or clap rejects it.
        sb, req = make_sb()
        sb.configure_temperature()  # default fs=1.0
        self.assertEqual(tok(req.last), tok("configure temperature --fs 1"))
        sb.configure_temperature(fs=0.1)
        self.assertEqual(tok(req.last), tok("configure temperature --fs 0.1"))

    def test_toggle_configs(self):
        sb, req = make_sb()
        sb.set_onboard_filtering(True)
        self.assertEqual(tok(req.last), tok("configure filtering on"))
        sb.set_high_gain(False)
        self.assertEqual(tok(req.last), tok("configure high-gain off"))
        sb.set_low_latency_mode(True)
        self.assertEqual(tok(req.last), tok("configure low-latency on"))
        sb.set_night_mode(False)
        self.assertEqual(tok(req.last), tok("configure night off"))

    def test_set_memory_mode(self):
        sb, req = make_sb()
        sb.set_memory_mode(MemoryMode.BOTH)
        self.assertEqual(tok(req.last), tok("configure memory both"))
        sb.set_memory_mode("streaming")
        self.assertEqual(tok(req.last), tok("configure memory streaming"))

    def test_set_ble_power(self):
        sb, req = make_sb()
        sb.set_ble_power(BleTxPower.HIGH)
        self.assertEqual(tok(req.last), tok("configure ble-power high"))
        sb.set_ble_power("low")
        self.assertEqual(tok(req.last), tok("configure ble-power low"))


class TestDeviceCommands(unittest.TestCase):
    def test_set_motor_intensity(self):
        sb, req = make_sb({"motor": {"ok": True}})
        ret = sb.set_motor_intensity(5)
        self.assertEqual(tok(req.last), tok("motor --intensity 5"))
        self.assertEqual(ret, {"ok": True})

    def test_set_motor_intensity_validation(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.set_motor_intensity(0)
        with self.assertRaises(ValueError):
            sb.set_motor_intensity(11)

    def test_set_motor(self):
        sb, req = make_sb()
        sb.set_motor(True)
        self.assertEqual(tok(req.last), tok("motor --state on"))
        sb.set_motor(False)
        self.assertEqual(tok(req.last), tok("motor --state off"))

    def test_set_led(self):
        sb, req = make_sb({"led": {"connected": True}})
        ret = sb.set_led(2, True)
        self.assertEqual(tok(req.last), tok("led --state on 2"))
        self.assertEqual(ret, {"connected": True})

    def test_set_led_validation(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.set_led(3, True)

    def test_set_status_updates(self):
        sb, req = make_sb({"status_update": {"connected": True}})
        ret = sb.set_status_updates(True)
        self.assertEqual(tok(req.last), tok("status-update on"))
        self.assertEqual(ret, {"connected": True})

    def test_info(self):
        sb, req = make_sb({"info": {"id": "DEV1", "connected": True}})
        self.assertEqual(sb.info(), {"id": "DEV1", "connected": True})
        self.assertEqual(req.last, "info")

    def test_get_active_device(self):
        sb, req = make_sb({"info": {"id": "DEV1"}})
        self.assertEqual(sb.get_active_device(), "DEV1")

    def test_select_device(self):
        def resp(line):
            return {"info": {"id": "DEV9"}} if line == "info" else {}

        sb, req = make_sb(resp)
        self.assertEqual(sb.select_device("myname"), "DEV9")
        self.assertEqual(req.calls[0], "select myname")
        self.assertEqual(req.calls[1], "info")

    def test_select_device_rejects_spaces(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.select_device("has space")

    def test_rename_device(self):
        sb, req = make_sb({"rename": {"name": "x"}})
        sb.rename_device("newname")
        self.assertEqual(tok(req.last), tok("rename newname"))
        sb.rename_device(None)
        self.assertEqual(tok(req.last), tok("rename --reset"))

    def test_rename_device_rejects_spaces(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.rename_device("bad name")

    def test_list_devices(self):
        sb, req = make_sb({"list": {"devices": ["a", "b"]}})
        self.assertEqual(sb.list_devices(ListSources.DEVICES), ["a", "b"])
        self.assertEqual(req.last, "list devices")
        sb2, req2 = make_sb({"list": {"devices": []}})
        sb2.list_devices("ble")
        self.assertEqual(req2.last, "list ble")

    def test_connect(self):
        sb, req = make_sb({"connect": {"connected": True}})
        self.assertTrue(sb.connect("bipi"))
        self.assertEqual(tok(req.last), tok("connect bipi"))
        self.assertEqual(req.timeouts[-1], 10.0)

    def test_connect_none_handle(self):
        sb, req = make_sb({"connect": {"connected": False}})
        self.assertFalse(sb.connect())
        self.assertEqual(req.calls[0].strip(), "connect")

    def test_connect_timeout_returns_false(self):
        def resp(line):
            raise SifiBridgeTimeout("no response")

        sb, req = make_sb(resp)
        self.assertFalse(sb.connect("x"))

    def test_disconnect(self):
        sb, req = make_sb({"disconnect": {"connected": False}})
        self.assertFalse(sb.disconnect())
        self.assertEqual(req.last, "disconnect")

    def test_start_stop_event(self):
        sb, req = make_sb(
            {
                "start": {"connected": True},
                "stop": {"connected": True},
                "event": {"connected": True},
            }
        )
        self.assertTrue(sb.start())
        self.assertEqual(req.last.strip(), "start")
        sb.start(all=True)
        self.assertEqual(tok(req.last), tok("start --all"))
        self.assertTrue(sb.stop())
        self.assertEqual(req.last.strip(), "stop")
        sb.stop(all=True)
        self.assertEqual(tok(req.last), tok("stop --all"))
        self.assertTrue(sb.send_event())
        self.assertEqual(req.last.strip(), "event")
        sb.send_event(all=True)
        self.assertEqual(tok(req.last), tok("event --all"))

    def test_erase_and_power_off(self):
        sb, req = make_sb()
        sb.erase_onboard_memory()
        self.assertEqual(req.last, "erase-memory")
        sb.power_off()
        self.assertEqual(req.last, "power-off")

    def test_error_response_propagates(self):
        def resp(line):
            raise SifiBridgeError("boom")

        sb, req = make_sb(resp)
        with self.assertRaises(SifiBridgeError):
            sb.configure_ecg()


class TestBufferCommands(unittest.TestCase):
    def test_buffer_export(self):
        sb, req = make_sb({"buffer_export": {"format": "csv", "files": ["a.csv"]}})
        ret = sb.buffer_export(fmt="csv", output_dir="/tmp/x", device="DEV")
        self.assertEqual(
            tok(req.last),
            tok("buffer export --handle DEV --dir /tmp/x --format csv"),
        )
        self.assertEqual(ret, {"format": "csv", "files": ["a.csv"]})

    def test_buffer_export_no_device(self):
        sb, req = make_sb({"buffer_export": {}})
        sb.buffer_export()
        self.assertNotIn("--handle", req.last)
        self.assertEqual(
            tok(req.last), tok("buffer export --dir . --format csv")
        )

    def test_buffer_list(self):
        sb, req = make_sb({"buffer_list": {"acquisitions": [{"id": 1}]}})
        self.assertEqual(sb.buffer_list(), [{"id": 1}])
        self.assertEqual(tok(req.last), tok("buffer list"))
        sb.buffer_list(device="DEV")
        self.assertEqual(tok(req.last), tok("buffer list --handle DEV"))

    def test_buffer_info(self):
        sb, req = make_sb({"buffer_info": {"acquisition": {}, "device_config": None}})
        sb.buffer_info(device="DEV", acquisition_id=2)
        self.assertEqual(
            tok(req.last), tok("buffer info --handle DEV --id 2")
        )

    def test_buffer_pull_single_sensor(self):
        sb, req = make_sb({"buffer_pull": {"sensors": [{"sensor": "ecg"}]}})
        ret = sb.buffer_pull("ecg")
        self.assertEqual(tok(req.last), tok("buffer pull --sensor ecg"))
        self.assertEqual(ret, [{"sensor": "ecg"}])

    def test_buffer_pull_multi_sensor_and_range(self):
        sb, req = make_sb({"buffer_pull": {"sensors": []}})
        sb.buffer_pull(
            ["ecg", "imu"], device="DEV", acquisition_id=1, from_=1.0, to=3.0
        )
        self.assertEqual(
            tok(req.last),
            tok(
                "buffer pull --handle DEV --sensor ecg --sensor imu "
                "--id 1 --from 1.0 --to 3.0"
            ),
        )

    def test_buffer_pull_last_seconds(self):
        sb, req = make_sb({"buffer_pull": {"sensors": []}})
        sb.buffer_pull("ecg", last_seconds=5)
        self.assertIn("--last-seconds 5", req.last)

    def test_buffer_pull_validations(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.buffer_pull("ecg", last_seconds=5, from_=1, to=2)
        with self.assertRaises(ValueError):
            sb.buffer_pull("ecg", from_=1)  # to missing
        with self.assertRaises(ValueError):
            sb.buffer_pull([])  # no sensors

    def test_buffer_clear_selectors(self):
        sb, req = make_sb({"buffer_clear": {"message": "ok"}})
        sb.buffer_clear(all=True)
        self.assertEqual(tok(req.last), tok("buffer clear --all"))
        sb.buffer_clear(device="DEV")
        self.assertEqual(tok(req.last), tok("buffer clear --handle DEV"))
        sb.buffer_clear(acquisition_id=3)
        self.assertEqual(tok(req.last), tok("buffer clear --id 3"))

    def test_buffer_clear_rejects_multiple_selectors(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.buffer_clear(all=True, device="DEV")

    def test_download_memory_ble(self):
        def resp(line):
            if line.startswith("buffer export"):
                return {"buffer_export": {"files": []}}
            if line == "info":
                return {"info": {"id": "DEV"}}
            return {}

        sb, req = make_sb(resp)
        sb.download_memory_ble("/tmp/o", fmt="hdf5")
        self.assertEqual(req.calls[0], "download-memory")
        self.assertIn("buffer export", req.calls[-1])
        self.assertIn("--handle DEV", req.calls[-1])
        self.assertIn("--format hdf5", req.calls[-1])

    def test_download_memory_serial(self):
        def resp(line):
            if line.startswith("buffer export"):
                return {"buffer_export": {"files": []}}
            if line == "info":
                return {"info": {"id": "DEV"}}
            return {}

        sb, req = make_sb(resp)
        sb.download_memory_serial("COM3", "/tmp/o")
        self.assertEqual(req.calls[0], "download-memory --serial COM3")
        self.assertIn("buffer export", req.calls[-1])


if __name__ == "__main__":
    unittest.main()
