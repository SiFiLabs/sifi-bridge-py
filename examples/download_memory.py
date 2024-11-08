import sifi_bridge_py as sbp
import logging

from sifi_bridge_py.sifi_bridge import DeviceType


def main():
    sb = sbp.SifiBridge()
    while not sb.connect(DeviceType.BIOPOINT_V1_3):
        continue

    kb = sb.start_memory_download()
    print(f"Start memory download for {kb} KB")

    ecg_data = []

    while True:
        data = sb.get_data()

        if data["status"] == "MemoryDownloadCompleted":
            break
        elif data["packet_type"] == "ecg":
            ecg_data.extend(data["data"]["ecg"])

    print(f"Downloaded {len(ecg_data)} samples of ECG.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    main()
