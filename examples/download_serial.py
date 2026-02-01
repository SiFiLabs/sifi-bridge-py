import sifi_bridge_py as sbp
import logging
import os

from sifi_bridge_py.sifi_bridge import DeviceType


def main():
    OUTPUT_DIR = "./"
    sb = sbp.SifiBridge()

    kb = sb.download_memory_serial("/dev/ttyACM0", "./")
    print(kb)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    main()
