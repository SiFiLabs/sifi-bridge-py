import sifi_bridge_py as sbp
import logging
import time


def main():
    sb = sbp.SifiBridge()
    while not sb.connect():
        continue

    print(sb.configure_sensors(ecg=True))

    kb = sb.start_memory_download()
    print(f"Start memory download for {kb} KB")

    ecg_data = []
    pkt_number = 0
    t0 = time.time()
    while True:
        data = sb.get_data()
        # print(data)
        pkt_number += 1
        if sb.is_memory_download_completed(data):
            break
        elif data["packet_type"] == "ecg":
            ecg_data.extend(data["data"]["ecg"])
    dt = time.time() - t0
    print(
        f"Downloaded {(pkt_number - 1) * 227 / 1000:.3f} kB ({pkt_number * 227 / (1000 * dt):.2f} kBps)"
    )
    print(f"Downloaded {len(ecg_data)} samples of ECG in {dt:.2f} seconds")
    sb.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main()
