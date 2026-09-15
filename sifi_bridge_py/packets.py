"""Typed views over the JSON sifibridge sends on the data channel.

Every enum here mirrors one that ``sifibridge schema`` exports, so the values
are the binary's own rather than a transcription of the docs.
``tests/test_integration.py`` re-exports those schemas from the pinned binary
and fails if the two ever disagree — the same idea as the command-line parse
check, applied to the data direction.

`DataPacket` is an opt-in convenience: the `SifiBridge` getters still return
plain dicts, and `DataPacket.from_dict` wraps one when attribute access reads
better than key lookups.

    >>> packet = sb.get_emg(timeout=2.0)
    >>> if packet:
    ...     emg = DataPacket.from_dict(packet)
    ...     print(emg.sample_rate, emg.channel(BioChannel.EMG)[:4])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

import numpy as np


class PacketType(Enum):
    """
    Data packet types that can be received from SiFi Bridge.

    Every member here is declared by the ``PacketType`` schema the binary
    exports, so none of them is unused even though the wrapper itself never
    mentions some: they are the vocabulary a caller matches incoming packets
    against. ``tests/test_integration.py`` fails if a member goes missing.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_ecg()
    >>> print(packet["packet_type"] == PacketType.ECG.value)
    True
    ```
    """

    ECG = "ecg"
    EMG = "emg"
    EDA = "eda"
    IMU = "imu"
    PPG = "ppg"
    EMG_ARMBAND = "emg_armband"
    LOW_LATENCY = "low_latency"
    TEMPERATURE = "temperature"
    MEMORY = "memory"
    STATUS = "status"
    START_TIME = "start_time"
    START_PACKET = "start_packet"
    DEVICE_INFO = "device_info"
    EVENT = "event"
    INVALID = "invalid"


class PacketStatus(Enum):
    """
    Data packet statuses.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_ecg()
    >>> print(packet["status"] == PacketStatus.OK.value)
    True
    ```
    """

    OK = "ok"
    RECORDING = "recording"
    MEMORY_DOWNLOAD_COMPLETED = "memory_download_completed"
    MEMORY_ERASED = "memory_erased"
    BAD_PAGE_INDEX = "bad_page_index"
    BAD_PACKET_LENGTH = "bad_packet_length"
    INVALID_DATETIME = "invalid_datetime"
    """The device's clock reported a date that does not exist (an impossible
    date, or an hour skipped by a daylight-saving change). The acquisition
    keeps recording, but the packet carries no usable ``start_time``."""
    INVALID = "invalid"


class DeviceType(Enum):
    """
    Device types reported in the `device` field of responses and packets.

    sifibridge 2.0.0 collapsed the per-revision BioPoint types into a single
    `BioPoint`; the hardware and firmware revisions are reported separately as
    `hardware_version` / `firmware_version` in `SifiBridge.get_configuration()`.
    The `BIOPOINT_V1_*` members are kept so code reading data recorded with
    sifibridge 1.x still resolves, but 2.0.0 never emits them.

    Passing one of these to `SifiBridge.connect()` still works as a BLE-name
    handle, but custom names (see `SifiBridge.rename_device()`) are preferred.
    """

    BIOPOINT = "BioPoint"
    SIFIBAND = "SiFiBand"
    SIFIBAND_FOCUS = "SiFiBandFocus"
    INVALID = "Invalid"
    """Not identified. The bridge treats such a device permissively rather
    than as having no hardware."""

    BIOPOINT_V1_1 = "BioPoint_v1_1"
    """DEPRECATED: emitted by sifibridge 1.x only."""
    BIOPOINT_V1_2 = "BioPoint_v1_2"
    """DEPRECATED: emitted by sifibridge 1.x only."""
    BIOPOINT_V1_3 = "BioPoint_v1_3"
    """DEPRECATED: emitted by sifibridge 1.x only."""


class BioChannel(Enum):
    """Every channel name that can appear as a key of a packet's ``data``.

    A packet carries only the channels its own sensor produces — an `imu`
    packet has the quaternion and acceleration channels, a `status` packet has
    `BATTERY` and `MEMORY_USED_KBYTES`, and so on.
    """

    ECG = "ecg"
    EMG = "emg"
    EDA = "eda"

    IMU = "imu"
    AX = "ax"
    AY = "ay"
    AZ = "az"
    QW = "qw"
    QX = "qx"
    QY = "qy"
    QZ = "qz"

    PPG = "ppg"
    IR = "ir"
    RED = "r"
    GREEN = "g"
    BLUE = "b"

    EMG0 = "emg0"
    EMG1 = "emg1"
    EMG2 = "emg2"
    EMG3 = "emg3"
    EMG4 = "emg4"
    EMG5 = "emg5"
    EMG6 = "emg6"
    EMG7 = "emg7"

    TEMPERATURE = "temperature"
    EVENT = "event"
    """Event kind: 0 null, 1 button press, 2 software event (`send_event`)."""

    BATTERY = "battery_%"
    MEMORY_USED_KBYTES = "memory_used_kbytes"

    BAD_PAGE_INDEX = "bad_page_index"
    BAD_PAGE_TOTAL = "bad_page_total"
    TEST_PROGRESS = "test_progress"
    """Flash self-test diagnostics."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"
    SECOND = "second"


class SensorChannel(Enum):
    """
    The channel names each sensor's packets carry, grouped by sensor.

    Use `BioChannel` for a single channel; this enum is the per-sensor grouping,
    handy for iterating over everything one packet contains.

    # Example

    ```python
    >>> sb = SifiBridge()
    >>> sb.connect()
    >>> sb.start()
    >>> packet = sb.get_imu()
    >>> imu = packet["data"]
    >>> print(len(imu) == len(SensorChannel.IMU.value)) # 7 IMU channels
    True
    >>> qw = imu[SensorChannel.IMU.value[0]] # get first channel
    >>> print(len(qw), qw)
    8 [0.5427, 0.5423, 0.5426, 0.5424, 0.5424, 0.5428, 0.5424, 0.5422]
    ```
    """

    ECG = "ecg"
    """ECG sensor channel."""
    EMG = "emg"
    """EMG sensor channel."""
    EMG_ARMBAND = ("emg0", "emg1", "emg2", "emg3", "emg4", "emg5", "emg6", "emg7")
    """SiFiBand 8-channel EMG sensor channels."""
    EDA = "eda"
    """EDA/BIOZ sensor channel."""
    IMU = ("qw", "qx", "qy", "qz", "ax", "ay", "az")
    """IMU sensor channels."""
    PPG = ("ir", "r", "g", "b")
    """PPG sensor channels."""
    TEMPERATURE = "temperature"
    """Temperature sensor channel."""
    EVENT = "event"
    """Event data key"""


def _channel_key(channel: BioChannel | str) -> str:
    return channel.value if isinstance(channel, BioChannel) else channel


@dataclass(frozen=True)
class DataPacket:
    """One packet from the data channel, as attributes instead of dict keys.

    Purely a convenience view: `SifiBridge`'s getters return plain dicts, and
    `from_dict` wraps one. The original dict is kept on `raw`, so a field this
    class does not model is never lost.

    Fields mirror the ``DataPacket`` schema that ``sifibridge schema`` exports.
    `packet_type` and `status` stay plain strings — the schema allows values
    outside the known sets, so an unrecognised one must not raise — and compare
    against `PacketType` / `PacketStatus` members' ``.value``, or via
    `is_type`.
    """

    packet_type: str
    status: str
    device: str
    id: str
    name: str
    mac: str
    received_at: float
    data: dict = field(default_factory=dict)
    timestamps: tuple = ()
    sample_rate: float | None = None
    """Measured live, so it is absent on the first packet of a stream: the rate
    takes two samples to establish. None means "not yet known", not zero."""
    samples_lost: int = 0
    start_time: float | None = None
    """Only on a Start Time packet: the acquisition start, as a Unix epoch
    timestamp. None when the device's clock was invalid."""
    device_state: str | None = None
    download_progress: int | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, packet: dict) -> "DataPacket":
        """Wrap a packet dict from `SifiBridge.get_data` or a typed getter.

        :raises ValueError: If `packet` is empty. The getters return ``{}`` on
            timeout, which is not a packet — test for it before wrapping.
        """
        if not packet:
            raise ValueError(
                "Empty packet: the getters return {} on timeout, so check the "
                "packet before wrapping it"
            )
        timestamps = packet.get("timestamps") or ()
        return cls(
            packet_type=packet.get("packet_type", PacketType.INVALID.value),
            status=packet.get("status", PacketStatus.INVALID.value),
            device=packet.get("device", ""),
            id=packet.get("id", ""),
            name=packet.get("name", ""),
            mac=packet.get("mac", ""),
            received_at=packet.get("received_at", 0.0),
            data=packet.get("data") or {},
            timestamps=tuple(timestamps) if not isinstance(timestamps, dict) else (),
            sample_rate=packet.get("sample_rate"),
            samples_lost=packet.get("samples_lost") or 0,
            start_time=packet.get("start_time"),
            device_state=packet.get("device_state"),
            download_progress=packet.get("download_progress"),
            raw=packet,
        )

    @property
    def channels(self) -> tuple:
        """The channel names this packet carries, in the order sent."""
        return tuple(self.data)

    @property
    def is_ok(self) -> bool:
        """Whether the packet reports a healthy status."""
        return self.status == PacketStatus.OK.value

    def is_type(self, packet_type: PacketType | str) -> bool:
        """Whether this packet is of the given type."""
        if isinstance(packet_type, PacketType):
            packet_type = packet_type.value
        return self.packet_type == packet_type

    def channel(self, channel: BioChannel | str) -> list:
        """The samples for one channel.

        :param channel: A `BioChannel`, or the raw channel name.
        :raises KeyError: If this packet does not carry that channel. Use
            `channels` to see what it has.
        """
        key = _channel_key(channel)
        try:
            return self.data[key]
        except KeyError:
            raise KeyError(
                f"Packet of type {self.packet_type!r} has no channel {key!r}; "
                f"it carries {sorted(self.data)}"
            ) from None

    def get(self, channel: BioChannel | str, default=None):
        """The samples for one channel, or `default` if it is not present."""
        return self.data.get(_channel_key(channel), default)

    def as_array(self, channels: Sequence[BioChannel | str] | None = None):
        """Stack the packet's channels into one 2-D array.

        :param channels: Which channels, in which order. Defaults to every
            channel the packet carries, in the order sent.
        :return: An array of shape ``(len(channels), n_samples)``.
        :raises KeyError: If a named channel is not present.
        """
        names = list(self.channels) if channels is None else list(channels)
        return np.array([self.channel(name) for name in names], dtype=float)

    def absolute_timestamps(self, start_time: float):
        """This packet's sample timestamps as Unix epoch times.

        Sample timestamps are relative to the start of the acquisition, in
        seconds. `start_time` comes from the acquisition's Start Time packet —
        see `sifi_bridge_py.utils.get_start_time`.

        :return: One absolute timestamp per sample, empty if the packet carries
            none.
        """
        if not self.timestamps:
            return np.array([], dtype=float)
        return np.asarray(self.timestamps, dtype=float) + start_time
