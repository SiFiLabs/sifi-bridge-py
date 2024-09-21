import sifi_bridge_py as sbp
import numpy as np
import logging


def main():
    sb = sbp.SifiBridge()
    while not sb.connect():
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

    np.savetxt("ecg_data.csv", ecg_data, delimiter=",")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    main()
