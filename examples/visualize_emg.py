from sifi_bridge_py import SifiBridge, DeviceName
import logging

import matplotlib.pyplot as plt


def main():
    device_type = DeviceName.BIOARMBAND

    logging.basicConfig(level=logging.INFO)

    sb = SifiBridge()
    while not sb.connect(device_type):
        continue
    sb.set_channels(emg=True)
    sb.configure_emg((20, 450), 50)
    sb.start()

    emg_data = (
        {f"emg{i}": [] for i in range(8)}
        if device_type == DeviceName.BIOARMBAND
        else {"emg": []}
    )
    base_key = "emg0" if device_type == DeviceName.BIOARMBAND else "emg"

    while len(emg_data[base_key]) < 10000:
        new_data = sb.get_emg()
        print(f"Sampling rate: {new_data['sample_rate']:.2f}")
        for e, v in new_data["data"].items():
            emg_data[e].extend(v)

    time = [i / new_data["sample_rate"] for i in range(len(emg_data[base_key]))]

    if device_type == DeviceName.BIOARMBAND:
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
    main()
