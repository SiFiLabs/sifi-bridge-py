from __future__ import annotations

import numpy as np


def get_attitude_from_quats(qw, qx, qy, qz):
    """
    Calculate attitude from quaternions.

    :return: pitch, yaw, roll in radians.
    """
    quats = np.array([qw, qx, qy, qz]).reshape(4, -1)
    quats /= np.linalg.norm(quats, axis=0)
    qw, qx, qy, qz = quats
    yaw = np.arctan2(2.0 * (qy * qz + qw * qx), qw * qw - qx * qx - qy * qy + qz * qz)
    aasin = qx * qz - qw * qy
    pitch = np.arcsin(-2.0 * aasin)
    roll = np.arctan2(2.0 * (qx * qy + qw * qz), qw * qw + qx * qx - qy * qy - qz * qz)
    return pitch, yaw, roll


def get_start_time(packet: dict) -> float | None:
    """
    Read the acquisition start time out of a Start Time packet.

    sifibridge emits one ``start_time`` packet at the beginning of every
    acquisition.

    :param packet: A packet whose ``packet_type`` is ``start_time``.
    :return: The acquisition start as a Unix epoch timestamp, or None if the
        packet does not contain the ``start_time`` value.
    """
    return packet.get("start_time")


def absolute_timestamps(packet: dict, start_time: float) -> np.ndarray:
    """
    Convert a data packet's sample timestamps to Unix epoch time.

    Sample timestamps are **relative to the start of the acquisition**, in
    seconds.

    :param packet: Any data packet carrying a ``timestamps`` array.
    :param start_time: The acquisition start, from `get_start_time`.
    :return: One absolute Unix epoch timestamp per sample. Empty if the packet
        carries no timestamps.

    # Example

    ```python
    >>> start = None
    >>> while start is None:
    ...     packet = sb.get_data()
    ...     if packet.get("packet_type") == "start_time":
    ...         start = get_start_time(packet)
    >>> emg = sb.get_emg()
    >>> t = absolute_timestamps(emg, start)
    ```
    """
    timestamps = packet.get("timestamps")
    if timestamps is None:
        return np.array([], dtype=float)
    return np.asarray(timestamps, dtype=float) + start_time
