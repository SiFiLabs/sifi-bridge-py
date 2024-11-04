import sifi_bridge_py as sbp
import logging
import os

from sifi_bridge_py.sifi_bridge import DeviceType


def main():
    OUTPUT_DIR = "./"
    sb = sbp.SifiBridge(data_transport="csv://./")
    while not sb.connect(DeviceType.BIOPOINT_V1_3):
        continue

    kb = sb.start_memory_download()
    print(f"Start memory download for {kb} KB")

    while True:
        data = sb.get_data()

        if data["status"] == "MemoryDownloadCompleted":
            break

    print("Finished downloading device memory:")
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(".csv"):
            print(f"{f}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    main()
