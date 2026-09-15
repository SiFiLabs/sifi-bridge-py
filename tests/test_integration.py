"""Tier 2 integration tests: real sifibridge subprocess, no hardware.

These spawn an actual sifibridge process and exercise everything that works
without a connected device: the subprocess/queue/socket plumbing, the commands
that don't need a device (``list``, ``buffer list``, ``buffer clear --all``),
and the error paths (device commands with no device must raise
``SifiBridgeError``). This validates the real JSON wire format and response-key
unwrapping end to end.

The binary is resolved by ``sifibridge_bin.get_executable()`` — set
``SIFIBRIDGE_EXE`` to point at a dev build (e.g. ``./bin/sifibridge``). The
whole module skips if no binary is available, unless ``SIFI_REQUIRE_BRIDGE``
is set, which turns that skip into a failure. CI sets it: a Tier 2 job that
skipped everything still reports OK, so without it a broken ``SIFIBRIDGE_EXE``
looks exactly like a passing run.

Tests that need a connected device live in ``test_hardware.py`` (gated on the
``SIFI_HW`` env var) and are not run here.
"""

import dataclasses
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import sifi_bridge_py as sbp
from sifi_bridge_py.sifi_bridge import PpgSensitivity, SifiBridgeError

from .test_unit import make_sb


def _no_bridge(reason: str):
    """What to do when the sifibridge binary cannot be used.

    Skipping is right locally — not every checkout has a binary — but in CI it
    turns a Tier 2 job that tested nothing into a green one, which is how a
    broken `SIFIBRIDGE_EXE` went unnoticed on the Windows runner. Set
    ``SIFI_REQUIRE_BRIDGE=1`` where the binary is supposed to be present and
    the same condition fails instead.

    :raises RuntimeError: If ``SIFI_REQUIRE_BRIDGE`` is set.
    :raises unittest.SkipTest: Otherwise.
    """
    if os.environ.get("SIFI_REQUIRE_BRIDGE"):
        raise RuntimeError(
            f"SIFI_REQUIRE_BRIDGE is set but the binary is unusable: {reason}"
        )
    raise unittest.SkipTest(reason)


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.sb = sbp.SifiBridge()
        except Exception as e:  # binary missing / wrong platform
            _no_bridge(f"sifibridge binary unavailable: {e}")

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


# Every wrapper method and the arguments to exercise it with. The point is
# coverage of the *generated command line*, not of the outcome, so one
# representative call per distinct flag combination is enough.
COMMAND_MATRIX = [
    ("configure_sensors", (), {"ecg": True, "emg": False}),
    ("configure_sensors", (), {}),
    ("configure_ecg", (), {"fs": 1000}),
    (
        "configure_ecg",
        (),
        {
            "fs": 500,
            "dc_notch": True,
            "mains_notch": 50,
            "bandpass": True,
            "flo": 0,
            "fhi": 30,
        },
    ),
    ("configure_ecg", (), {"mains_notch": "off"}),
    ("configure_ecg", (), {"mains_notch": 60}),
    ("configure_emg", (), {"fs": 2000, "bandpass": False}),
    ("configure_eda", (), {"fs": 50, "freq": 0}),
    ("configure_ppg", (), {"sps": 400, "avg": 2}),
    ("configure_ppg", (), {"ir": 9, "red": 9, "green": 9, "blue": 9}),
    ("configure_ppg", (), {"sens": PpgSensitivity.MAX}),
    ("configure_ppg_fs", (100,), {}),
    ("configure_ppg_fs", (25,), {"avg": 32}),
    ("configure_imu", (), {"fs": 100, "accel_range": 16}),
    ("configure_imu", (), {"accel_range": 8}),
    ("configure_temperature", (), {"fs": 1.0}),
    ("configure_temperature", (), {"fs": 0.1}),
    ("set_onboard_filtering", (True,), {}),
    ("set_high_gain", (False,), {}),
    ("set_memory_mode", ("both",), {}),
    ("set_low_latency_mode", (True,), {}),
    ("set_ble_power", ("high",), {}),
    ("set_night_mode", (False,), {}),
    ("set_motor_intensity", (1,), {}),
    ("set_motor_intensity", (10,), {}),
    ("set_motor", (True,), {}),
    ("set_led", (1, True), {}),
    ("set_led", (2, False), {}),
    ("set_status_updates", (True,), {}),
    ("erase_onboard_memory", (), {}),
    ("erase_onboard_memory", (), {"format": True}),
    ("power_off", (), {}),
    ("start", (), {}),
    ("start", (), {"set_default": True}),
    ("stop", (), {}),
    ("send_event", (), {}),
    ("rename_device", ("shortname",), {}),
    ("rename_device", (None,), {}),
    ("connect", ("BioPoint",), {}),
    ("disconnect", (), {}),
    ("info", (), {}),
    ("buffer_export", (), {"fmt": "csv", "output_dir": "."}),
    ("buffer_export", (), {"fmt": "hdf5", "output_dir": "."}),
    ("buffer_list", (), {}),
    ("buffer_info", (), {"acquisition_id": 1}),
    ("buffer_pull", ("ecg",), {}),
    ("buffer_pull", (["ecg", "imu"],), {"last_seconds": 5}),
    ("buffer_pull", ("emg",), {"from_": 1.0, "to": 3.0}),
    ("buffer_clear", (), {"all": True}),
    ("buffer_clear", (), {"acquisition_id": 1}),
    ("dfu", ("nonexistent-package.zip",), {}),
    ("download_memory_ble", ("."), {}),
    ("download_memory_serial", ("COM3", "."), {}),
]

# The same matrix again, targeted at several devices. `--all` and `--devices`
# sit in different places per command (`configure` takes them before its
# subcommand), which is exactly the kind of thing a parse check catches.
TARGETED_MATRIX = [
    ("configure_ecg", (), {"fs": 1000, "all": True}),
    ("configure_ecg", (), {"fs": 1000, "devices": ["dev1", "dev2"]}),
    ("configure_sensors", (), {"emg": True, "all": True}),
    ("configure_imu", (), {"fs": 50, "all": True}),
    ("set_memory_mode", ("streaming",), {"all": True}),
    ("set_night_mode", (True,), {"devices": "dev1"}),
    ("set_led", (1, True), {"all": True}),
    ("set_motor", (False,), {"all": True}),
    ("set_motor_intensity", (5,), {"all": True}),
    ("set_status_updates", (False,), {"all": True}),
    ("erase_onboard_memory", (), {"all": True}),
    ("power_off", (), {"all": True}),
    ("start", (), {"all": True}),
    ("stop", (), {"devices": ["dev1", "dev2"]}),
    ("send_event", (), {"all": True}),
]


class TestGeneratedCommandsParse(unittest.TestCase):
    """Every command line the wrapper can generate must parse.

    The wrapper builds REPL command lines as strings, so a renamed or removed
    flag is invisible to Python and to the Tier 1 tests — both keep passing
    while the binary rejects every call. That is exactly how
    ``configure imu --gyro-range`` survived into the 2.0.0 betas after
    sifibridge dropped the flag.

    This drives each wrapper method with a recorder to capture the exact line
    it would send, then feeds that line to a real sifibridge and asserts the
    reply is not a *parse* error. No device is connected, so the commands fail
    with runtime errors ("device '' not found"), which is fine and expected:
    clap prefixes its own diagnostics with ``error: ``, and nothing else does.
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.sb = sbp.SifiBridge()
        except Exception as e:  # binary missing / wrong platform
            _no_bridge(f"sifibridge binary unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        sb = getattr(cls, "sb", None)
        if sb is not None:
            sb.close()

    @staticmethod
    def _lines_for(method: str, args, kwargs) -> list:
        """Capture the REPL line(s) a wrapper call would send."""
        sb, rec = make_sb()
        getattr(sb, method)(*args, **kwargs)
        return rec.calls

    def _assert_parses(self, line: str):
        try:
            self.sb._request(line, timeout=10.0)
        except SifiBridgeError as e:
            # clap renders its diagnostics with an "error: " prefix; runtime
            # failures ("device '' not found", "No device selected") do not.
            self.assertFalse(
                e.message.startswith("error: "),
                f"sifibridge could not parse {line!r}:\n{e.message}",
            )

    def test_generated_commands_parse(self):
        for method, args, kwargs in COMMAND_MATRIX + TARGETED_MATRIX:
            for line in self._lines_for(method, args, kwargs):
                with self.subTest(method=method, line=line):
                    self._assert_parses(line)

    def test_matrix_covers_every_public_command(self):
        """Guard against a new wrapper command escaping the parse check."""
        covered = {m for m, _, _ in COMMAND_MATRIX + TARGETED_MATRIX}
        # Methods that send no REPL command, or whose line is covered by
        # another entry (e.g. select_device -> `select` + `info`).
        exempt = {
            "close",
            "connect",
            "clear_data_buffer",
            "get_active_device",
            "get_battery",
            "get_configuration",
            "get_data",
            "get_device_state",
            "get_ecg",
            "get_eda",
            "get_emg",
            "get_event",
            "get_imu",
            "get_ppg",
            "get_sensor_states",
            "get_sensors",
            "get_temperature",
            "list_devices",
            "select_device",
        }
        public = {
            name
            for name in dir(sbp.SifiBridge)
            if not name.startswith("_") and callable(getattr(sbp.SifiBridge, name))
        }
        missing = public - covered - exempt
        self.assertEqual(missing, set(), f"not covered by the parse check: {missing}")


# Schema values the wrapper deliberately does not carry a member for.
#
# The bridge can emit these, but they are internal or diagnostic and the enums
# are kept to the surface a caller works with. They still arrive as plain
# strings in the packet, so nothing is lost — `DataPacket.packet_type` and
# `.status` are strings for exactly this reason, and unknown channel keys stay
# readable through `DataPacket.data`.
#
# The point of listing them here rather than loosening the check is that a
# value the bridge adds *later* still fails: this is an inventory of decisions,
# not a mute button.
SCHEMA_VALUES_NOT_MODELLED = {
    "PacketType": {"low_latency", "start_packet", "device_info"},
    "PacketStatus": {"bad_page_index", "bad_packet_length"},
    "BioChannel": {"bad_page_index", "bad_page_total", "test_progress"},
}


class TestSchemasMatchTheBinary(unittest.TestCase):
    """The enums must track what the binary says it can send.

    `sifibridge schema` exports the JSON schema for the packet types, statuses,
    device types and channel names. Comparing our enums against that export is
    the data-direction counterpart of the command-line parse check: a value
    added or renamed in the bridge shows up here instead of as a surprise
    string in a user's packet.

    Two kinds of difference are allowed, both by explicit list: extras on our
    side (sifibridge 1.x device types, kept so old recordings still resolve)
    and values we deliberately do not model (`SCHEMA_VALUES_NOT_MODELLED`).
    Anything else fails.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from sifibridge_bin import get_executable

            exe = get_executable()
        except Exception as e:  # binary missing / wrong platform
            _no_bridge(f"sifibridge binary unavailable: {e}")

        cls._tmp = tempfile.TemporaryDirectory()
        result = subprocess.run(
            [exe, "schema", "-o", cls._tmp.name],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            cls._tmp.cleanup()
            _no_bridge(
                f"`sifibridge schema` failed ({result.returncode}): "
                f"{result.stderr.decode(errors='replace')[:300]}"
            )
        cls.schema_dir = pathlib.Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def _schema_values(self, name: str) -> set:
        """Every string the named schema allows, from its enum branches.

        The schemas wrap their enum in `anyOf`/`oneOf` alongside an open
        `{"type": "string"}` branch for forward compatibility, so collect the
        `enum` and `const` entries wherever they appear.
        """
        path = self.schema_dir / f"{name}.schema.json"
        self.assertTrue(path.is_file(), f"{path.name} was not exported")
        schema = json.loads(path.read_text(encoding="utf-8"))

        values = set()

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("enum"), list):
                    values.update(v for v in node["enum"] if isinstance(v, str))
                if isinstance(node.get("const"), str):
                    values.add(node["const"])
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(schema)
        self.assertTrue(values, f"no enum values found in {path.name}")
        return values

    def _assert_covers(self, enum_cls, schema_name: str, allowed_extras=frozenset()):
        schema_values = self._schema_values(schema_name)
        ours = {member.value for member in enum_cls}
        not_modelled = SCHEMA_VALUES_NOT_MODELLED.get(schema_name, set())

        missing = schema_values - ours - not_modelled
        self.assertEqual(
            missing,
            set(),
            f"{enum_cls.__name__} has no member for {sorted(missing)}, which "
            f"the binary's {schema_name} schema says it can emit. Add the "
            f"member, or list the value in SCHEMA_VALUES_NOT_MODELLED if "
            f"leaving it out is deliberate",
        )

        # Keep that list honest: an entry the schema no longer mentions is
        # stale, and hides the next real omission behind it.
        stale = not_modelled - schema_values
        self.assertEqual(
            stale,
            set(),
            f"SCHEMA_VALUES_NOT_MODELLED[{schema_name!r}] lists {sorted(stale)}, "
            f"which the schema no longer declares; drop the entry",
        )
        unexpected = ours - schema_values - set(allowed_extras)
        self.assertEqual(
            unexpected,
            set(),
            f"{enum_cls.__name__} has {sorted(unexpected)}, which the binary's "
            f"{schema_name} schema does not list",
        )

    def test_packet_type(self):
        self._assert_covers(sbp.PacketType, "PacketType")

    def test_packet_status(self):
        self._assert_covers(sbp.PacketStatus, "PacketStatus")

    def test_bio_channel(self):
        self._assert_covers(sbp.BioChannel, "BioChannel")

    def test_device_type(self):
        # The per-revision BioPoint names are sifibridge 1.x only; kept so data
        # recorded with 1.x still resolves.
        self._assert_covers(
            sbp.DeviceType,
            "DeviceType",
            allowed_extras={"BioPoint_v1_1", "BioPoint_v1_2", "BioPoint_v1_3"},
        )

    def test_sensor_channel_names_are_bio_channels(self):
        """Every name in the per-sensor grouping must be a real channel."""
        known = {member.value for member in sbp.BioChannel}
        for sensor in sbp.SensorChannel:
            names = (
                (sensor.value,) if isinstance(sensor.value, str) else sensor.value
            )
            for name in names:
                with self.subTest(sensor=sensor.name, channel=name):
                    self.assertIn(name, known)

    def test_data_packet_fields_are_modelled(self):
        """`DataPacket` must have an attribute for every schema property."""
        schema = json.loads(
            (self.schema_dir / "DataPacket.schema.json").read_text(encoding="utf-8")
        )
        properties = set(schema.get("properties", {}))
        modelled = {f.name for f in dataclasses.fields(sbp.DataPacket)}
        missing = properties - modelled
        self.assertEqual(
            missing,
            set(),
            f"DataPacket does not model {sorted(missing)}; the fields are "
            "still reachable through .raw, but should be named",
        )


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
