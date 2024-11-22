import sifi_bridge_py as sbp
import logging
import time

from sifi_bridge_py.sifi_bridge import DeviceType


def main():
    sb = sbp.SifiBridge()
    while not sb.connect(DeviceType.BIOPOINT_V1_3):
        continue

    kb = sb.start_memory_download()
    print(f"Start memory download for {kb} KB")

    ecg_data = []
    pkt_number = 0
    t0 = time.time()
    while True:
        data = sb.get_data()
        pkt_number += 1
        if data["status"] == "MemoryDownloadCompleted":
            break
        elif data["packet_type"] == "ecg":
            ecg_data.extend(data["data"]["ecg"])
    dt = time.time() - t0
    print(f"Download throughput: {pkt_number*227 / (1000*dt):.2f} kBps")
    print(f"Downloaded {len(ecg_data)} samples of ECG in {dt:.2f} seconds")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    main()
