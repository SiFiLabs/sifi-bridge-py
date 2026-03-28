import logging
import matplotlib.pyplot as plt

from sifi_bridge_py import SifiBridge, DeviceType


def main():
    device_type = DeviceType.BIOPOINT_V1_3

    sb = SifiBridge()
    while not sb.connect(device_type):
        continue
    sb.configure_sensors(emg=True)
    sb.configure_emg(fs=1000, mains_notch=60)
    sb.stop()
    sb.start()

    emg_data = (
        {f"emg{i}": [] for i in range(8)}
        if device_type == DeviceType.SIFIBAND
        else {"emg": []}
    )
    time = []
    base_key = "emg0" if device_type == DeviceType.SIFIBAND else "emg"

    while len(emg_data[base_key]) < 10000:
        new_data = sb.get_emg()
        print(f"Sampling rate: {new_data['sample_rate']:.2f}")
        for e, v in new_data["data"].items():
            emg_data[e].extend(v)
        time.extend(new_data["timestamps"])

    if device_type == DeviceType.SIFIBAND:
        legend = [f"Channel {i}" for i in range(8)]
        for i in range(8):
            plt.plot(time, emg_data[f"emg{i}"])
        plt.legend(legend)
    else:
        plt.plot(time, emg_data[base_key])

    plt.ylabel("EMG (mV)")
    plt.xlabel("Time (s)")
    plt.show()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    main()
