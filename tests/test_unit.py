"""Tier 1 unit tests: pure wrapper logic, no subprocess and no hardware.

Every public command funnels through ``SifiBridge._request``. These tests
replace that one method with a recorder, which lets us assert two things
without spawning sifibridge or owning a device:

1. the exact REPL command line the wrapper sends (catches wrong flags, e.g.
   ``--device`` vs ``--handle`` or ``--iir`` vs ``--led-ir``), and
2. that the response key is unwrapped correctly (e.g. ``["connect"]["connected"]``).

Command lines are compared token-wise (``str.split()``) so incidental
whitespace is ignored while flags and values still must match exactly.

Note that a command line being well-formed *here* does not prove sifibridge
accepts it — ``tests/test_integration.py`` feeds every one of these lines to a
real binary for that.
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


def make_sb(response=None):
    """A SifiBridge with no subprocess: __init__ is skipped and ``_request`` is
    replaced by a recorder."""
    sb = SifiBridge.__new__(SifiBridge)
    rec = FakeRequest(response)
    sb._request = rec
    return sb, rec


def tok(line: str):
    return line.split()


class TestOptionalConfiguration(unittest.TestCase):
    """sifibridge 2.0.0 leaves a setting untouched when its flag is absent.

    The wrapper must therefore emit *only* the parameters the caller passed: a
    default-valued flag would silently overwrite a setting the caller never
    mentioned. These tests pin that down flag by flag — they are the unit-level
    half of the guarantee, with the round-trip half in ``test_hardware.py``.
    """

    def test_configure_sensors_emits_only_given_sensors(self):
        sb, req = make_sb()
        sb.configure_sensors(ppg=True)
        self.assertEqual(tok(req.last), tok("configure sensors --ppg on"))

    def test_configure_sensors_off_is_not_omission(self):
        sb, req = make_sb()
        sb.configure_sensors(ecg=False, imu=True)
        self.assertEqual(tok(req.last), tok("configure sensors --ecg off --imu on"))

    def test_configure_sensors_no_args_emits_no_flags(self):
        sb, req = make_sb()
        sb.configure_sensors()
        self.assertEqual(tok(req.last), tok("configure sensors"))

    def test_configure_ecg_fs_only(self):
        # The headline case: changing fs must not resend the filter settings.
        sb, req = make_sb()
        sb.configure_ecg(fs=1000)
        self.assertEqual(tok(req.last), tok("configure ecg --fs 1000"))

    def test_configure_emg_fs_only(self):
        sb, req = make_sb()
        sb.configure_emg(fs=1000)
        self.assertEqual(tok(req.last), tok("configure emg --fs 1000"))

    def test_configure_ecg_full(self):
        sb, req = make_sb()
        sb.configure_ecg(
            fs=500, dc_notch=True, mains_notch=50, bandpass=True, flo=0, fhi=30
        )
        self.assertEqual(
            tok(req.last),
            tok(
                "configure ecg --fs 500 --dc-notch on --mains-notch 50 "
                "--bandpass on --bandpass-low 0 --bandpass-high 30"
            ),
        )

    def test_configure_ecg_false_flags_are_emitted_as_off(self):
        sb, req = make_sb()
        sb.configure_ecg(dc_notch=False, bandpass=False)
        self.assertEqual(
            tok(req.last), tok("configure ecg --dc-notch off --bandpass off")
        )

    def test_configure_ecg_zero_cutoff_is_emitted(self):
        # flo=0 is a real value, not an omission.
        sb, req = make_sb()
        sb.configure_ecg(flo=0)
        self.assertEqual(tok(req.last), tok("configure ecg --bandpass-low 0"))

    def test_mains_notch_tristate(self):
        sb, req = make_sb()
        sb.configure_ecg(mains_notch=50)
        self.assertIn("--mains-notch 50", req.last)
        sb.configure_ecg(mains_notch=60)
        self.assertIn("--mains-notch 60", req.last)
        for disable in ("off", 0, False):
            sb.configure_ecg(mains_notch=disable)
            self.assertIn("--mains-notch off", req.last)
        sb.configure_ecg(mains_notch=None)
        self.assertNotIn("--mains-notch", req.last)

    def test_mains_notch_rejects_other_values(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.configure_ecg(mains_notch=100)

    def test_configure_eda_freq_optional(self):
        sb, req = make_sb()
        sb.configure_eda(freq=15)
        self.assertEqual(tok(req.last), tok("configure eda --freq 15"))
        sb.configure_eda(fs=50)
        self.assertEqual(tok(req.last), tok("configure eda --fs 50"))

    def test_configure_temperature_optional_and_float_format(self):
        # The binary accepts "0.1", "1", "2", "10"; a float 1.0 must render as
        # "1", not "1.0", or clap rejects it.
        sb, req = make_sb()
        sb.configure_temperature(fs=1.0)
        self.assertEqual(tok(req.last), tok("configure temperature --fs 1"))
        sb.configure_temperature(fs=0.1)
        self.assertEqual(tok(req.last), tok("configure temperature --fs 0.1"))
        sb.configure_temperature()
        self.assertEqual(tok(req.last), tok("configure temperature"))


class TestPpgConfiguration(unittest.TestCase):
    def test_configure_ppg_uses_led_flags(self):
        sb, req = make_sb()
        sb.configure_ppg(ir=9, red=9, green=9, blue=9, sens="medium")
        self.assertEqual(
            tok(req.last),
            tok(
                "configure ppg --led-ir 9 --led-red 9 --led-green 9 "
                "--led-blue 9 --sens medium"
            ),
        )

    def test_configure_ppg_sens_accepts_str_and_enum(self):
        sb, req = make_sb()
        sb.configure_ppg(sens="high")
        self.assertIn("--sens high", req.last)
        sb.configure_ppg(sens=PpgSensitivity.MAX)
        self.assertIn("--sens max", req.last)

    def test_configure_ppg_rate_within_cap(self):
        sb, req = make_sb()
        sb.configure_ppg(sps=400, avg=2)  # 200 Hz effective, at the cap
        self.assertEqual(tok(req.last), tok("configure ppg --sps 400 --avg 2"))

    def test_configure_ppg_rate_above_cap_rejected(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError) as ctx:
            sb.configure_ppg(sps=800, avg=2)  # 400 Hz effective
        self.assertIn("200", str(ctx.exception))

    def test_configure_ppg_requires_sps_and_avg_together(self):
        # Neither alone determines the effective rate, so the cap cannot be
        # enforced from one of them.
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.configure_ppg(sps=400)
        with self.assertRaises(ValueError):
            sb.configure_ppg(avg=2)

    def test_configure_ppg_rejects_non_positive_avg(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.configure_ppg(sps=100, avg=0)


class TestImuConfiguration(unittest.TestCase):
    def test_configure_imu_never_sends_gyro_range(self):
        # 2.0.0 removed --gyro-range; sending it is a hard parse error.
        sb, req = make_sb()
        sb.configure_imu(fs=200, accel_range=16)
        self.assertEqual(tok(req.last), tok("configure imu --fs 200 --acc-range 16"))
        self.assertNotIn("--gyro-range", req.last)

    def test_configure_imu_accel_range_validated(self):
        sb, req = make_sb()
        for bad in (2, 4, 32):
            with self.assertRaises(ValueError):
                sb.configure_imu(accel_range=bad)

    def test_configure_imu_fs_only(self):
        sb, req = make_sb()
        sb.configure_imu(fs=100)
        self.assertEqual(tok(req.last), tok("configure imu --fs 100"))


class TestDeviceTargeting(unittest.TestCase):
    """``--all`` / ``--devices`` and the aggregated ``multi`` response."""

    def test_configure_targets_precede_subcommand(self):
        # `configure ecg --fs 1000 --all` is rejected by the binary; the
        # targeting flags belong before the subcommand.
        sb, req = make_sb()
        sb.configure_ecg(fs=1000, all=True)
        self.assertEqual(tok(req.last), tok("configure --all ecg --fs 1000"))

    def test_configure_devices_list(self):
        sb, req = make_sb()
        sb.configure_emg(fs=1000, devices=["dev1", "dev2"])
        self.assertEqual(
            tok(req.last), tok("configure --devices dev1,dev2 emg --fs 1000")
        )

    def test_configure_devices_single_string(self):
        sb, req = make_sb()
        sb.configure_emg(fs=1000, devices="dev1")
        self.assertEqual(tok(req.last), tok("configure --devices dev1 emg --fs 1000"))

    def test_all_and_devices_are_mutually_exclusive(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.start(all=True, devices="dev1")

    def test_device_handles_are_validated(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.start(devices="has space")
        with self.assertRaises(ValueError):
            sb.start(devices=["ok", "has,comma"])

    def test_targets_on_device_commands(self):
        sb, req = make_sb()
        sb.set_led(1, True, all=True)
        self.assertEqual(tok(req.last), tok("led --state on 1 --all"))
        sb.set_status_updates(True, devices=["a", "b"])
        self.assertEqual(tok(req.last), tok("status-update on --devices a,b"))
        sb.set_motor(False, all=True)
        self.assertEqual(tok(req.last), tok("motor --state off --all"))
        sb.power_off(all=True)
        self.assertEqual(tok(req.last), tok("power-off --all"))

    def test_multi_response_is_returned_as_a_list(self):
        # Before this, `start(all=True)` raised KeyError: the aggregated
        # response has no "start" key.
        per_device = [{"id": "a", "connected": True}, {"id": "b", "connected": True}]
        sb, req = make_sb({"multi": {"responses": per_device}})
        self.assertEqual(sb.start(all=True), per_device)
        self.assertEqual(sb.stop(all=True), per_device)
        self.assertEqual(sb.send_event(all=True), per_device)
        self.assertEqual(sb.set_led(1, True, all=True), per_device)
        self.assertEqual(sb.erase_onboard_memory(all=True), per_device)
        self.assertEqual(sb.configure_ecg(fs=500, all=True), per_device)

    def test_single_device_response_is_unwrapped(self):
        sb, req = make_sb({"start": {"connected": True}})
        self.assertIs(sb.start(), True)


class TestDeviceCommands(unittest.TestCase):
    def test_set_motor_intensity(self):
        sb, req = make_sb({"motor": {"ok": True}})
        ret = sb.set_motor_intensity(5)
        self.assertEqual(tok(req.last), tok("motor --intensity 5"))
        self.assertEqual(ret, {"ok": True})

    def test_set_motor_intensity_validation(self):
        # The binary accepts 0-10, but the wrapper's contract is 1-10.
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.set_motor_intensity(0)
        with self.assertRaises(ValueError):
            sb.set_motor_intensity(11)

    def test_set_motor(self):
        sb, req = make_sb({"motor": {"connected": True}})
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

    def test_erase_memory_format_flag(self):
        sb, req = make_sb({"erase_memory": {"connected": True}})
        ret = sb.erase_onboard_memory()
        self.assertEqual(tok(req.last), tok("erase-memory"))
        self.assertEqual(ret, {"connected": True})
        sb.erase_onboard_memory(format=True)
        self.assertEqual(tok(req.last), tok("erase-memory --format"))

    def test_power_off_unwraps(self):
        sb, req = make_sb({"power_off": {"connected": True}})
        self.assertEqual(sb.power_off(), {"connected": True})
        self.assertEqual(tok(req.last), tok("power-off"))

    def test_start_set_default(self):
        sb, req = make_sb({"start": {"connected": True}})
        sb.start(set_default=True)
        self.assertEqual(tok(req.last), tok("start --set-default"))

    def test_start_stop_event(self):
        sb, req = make_sb(
            {
                "start": {"connected": True},
                "stop": {"connected": True},
                "event": {"connected": True},
            }
        )
        self.assertTrue(sb.start())
        self.assertEqual(tok(req.last), tok("start"))
        self.assertTrue(sb.stop())
        self.assertEqual(tok(req.last), tok("stop"))
        self.assertTrue(sb.send_event())
        self.assertEqual(tok(req.last), tok("event"))

    def test_dfu(self):
        sb, req = make_sb({"dfu": {"ok": True}})
        ret = sb.dfu("C:/fw/pkg.zip")
        self.assertEqual(tok(req.last), tok("dfu C:/fw/pkg.zip"))
        self.assertEqual(ret, {"ok": True})
        sb.dfu("pkg.zip", handle="dev1", resume=True)
        self.assertEqual(tok(req.last), tok("dfu --handle dev1 --resume pkg.zip"))

    def test_dfu_rejects_path_with_spaces(self):
        # The REPL splits on whitespace and does not support quoting.
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.dfu("C:/My Files/pkg.zip")

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


class TestSessionCommands(unittest.TestCase):
    def test_info(self):
        sb, req = make_sb({"info": {"id": "DEV1", "connected": True}})
        self.assertEqual(sb.info(), {"id": "DEV1", "connected": True})
        self.assertEqual(req.last, "info")

    def test_get_active_device(self):
        sb, req = make_sb({"info": {"id": "DEV1"}})
        self.assertEqual(sb.get_active_device(), "DEV1")

    def test_info_accessors(self):
        sensors = {"ecg": False, "emg": True}
        sb, req = make_sb(
            {
                "info": {
                    "sensors": sensors,
                    "device_state": "idle",
                    "configuration": {"emg": {"fs": 2000}},
                }
            }
        )
        self.assertEqual(sb.get_sensors(), sensors)
        self.assertEqual(sb.get_device_state(), "idle")
        self.assertEqual(sb.get_configuration(), {"emg": {"fs": 2000}})

    def test_info_accessors_tolerate_missing_fields(self):
        sb, req = make_sb({"info": {"id": "DEV1"}})
        self.assertEqual(sb.get_sensors(), {})
        self.assertIsNone(sb.get_device_state())
        self.assertEqual(sb.get_configuration(), {})

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

    def test_rename_device_enforces_14_byte_limit(self):
        sb, req = make_sb({"rename": {}})
        sb.rename_device("a" * 14)
        with self.assertRaises(ValueError):
            sb.rename_device("a" * 15)
        # The firmware limit is in bytes, not characters.
        with self.assertRaises(ValueError):
            sb.rename_device("é" * 8)

    def test_list_devices(self):
        devices = [{"id": "C2:EC:EF:34:0E:00", "name": "my_biopoint"}]
        sb, req = make_sb({"list": {"devices": devices}})
        self.assertEqual(sb.list_devices(ListSources.DEVICES), devices)
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

    def test_connect_rejects_handle_with_spaces(self):
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.connect("has space")

    def test_connect_timeout_returns_false(self):
        def resp(line):
            raise SifiBridgeTimeout("no response")

        sb, req = make_sb(resp)
        self.assertFalse(sb.connect("x"))

    def test_disconnect(self):
        sb, req = make_sb({"disconnect": {"connected": False}})
        self.assertFalse(sb.disconnect())
        self.assertEqual(req.last, "disconnect")

    def test_error_response_propagates(self):
        def resp(line):
            raise SifiBridgeError("boom")

        sb, req = make_sb(resp)
        with self.assertRaises(SifiBridgeError):
            sb.configure_ecg(fs=500)


class TestResponseRouting(unittest.TestCase):
    """``_request`` matches a response to its command by top-level key."""

    def test_expected_key_derivation(self):
        cases = {
            "info": "info",
            "status-update on": "status_update",
            "power-off": "power_off",
            "erase-memory --format": "erase_memory",
            "download-memory": "download_memory",
            "buffer list": "buffer_list",
            "buffer export --dir .": "buffer_export",
            "configure --all ecg --fs 1000": "configure",
            "": None,
        }
        for line, expected in cases.items():
            self.assertEqual(SifiBridge._expected_key(line), expected, line)


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
        self.assertEqual(tok(req.last), tok("buffer export --dir . --format csv"))

    def test_buffer_export_rejects_output_dir_with_spaces(self):
        # The REPL cannot express it: it splits on whitespace, no quoting.
        sb, req = make_sb()
        with self.assertRaises(ValueError):
            sb.buffer_export(output_dir="C:/Users/me/My Data")

    def test_buffer_list(self):
        sb, req = make_sb({"buffer_list": {"acquisitions": [{"id": 1}]}})
        self.assertEqual(sb.buffer_list(), [{"id": 1}])
        self.assertEqual(tok(req.last), tok("buffer list"))
        sb.buffer_list(device="DEV")
        self.assertEqual(tok(req.last), tok("buffer list --handle DEV"))

    def test_buffer_info(self):
        sb, req = make_sb({"buffer_info": {"acquisition": {}, "device_config": None}})
        sb.buffer_info(device="DEV", acquisition_id=2)
        self.assertEqual(tok(req.last), tok("buffer info --handle DEV --id 2"))

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
