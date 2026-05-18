import sifi_bridge_py as sbp
import logging
import time

from sifi_bridge_py.sifi_bridge import SifiBridgeError


def main():
    with sbp.SifiBridge() as sb:
        while not sb.connect():
            time.sleep(0.5)

        print("Connected !")

        sb.set_status_updates(True)

        for i in range(10):
            print(sb.get_data())
        # try:
        #     print(sb.rename_device())
        # except SifiBridgeError as sbe:
        #     print("Sifi bridge error:", sbe)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    main()
