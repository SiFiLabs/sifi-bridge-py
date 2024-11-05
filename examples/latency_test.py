import sifi_bridge_py as sb
import time
import numpy as np

def main():
    NUM_RUNS = 50
    NUM_TRIES = 50

    bridge = sb.SifiBridge()

    while not bridge.connect():
        print("Failed to connect...")
        time.sleep(1)
    bridge.send_command(sb.DeviceCommand.ERASE_ONBOARD_MEMORY)

    t_arr = []
    print("Starting experiment...")
    for i in range(NUM_RUNS):
        bridge.disconnect()
        while not bridge.connect():
            print(f"({i}) Failed to connect...")
            time.sleep(3)

        ti = time.perf_counter()
        for _ in range(NUM_TRIES):
            bridge.send_command(sb.DeviceCommand.DOWNLOAD_ONBOARD_MEMORY)
            bridge.get_data()
        t_arr.append((time.perf_counter() - ti) / NUM_TRIES)

        print(
            f"Run {i+1:2d}/{NUM_RUNS} Avg latency ({NUM_TRIES} tries): {t_arr[i]*1000:.2f} ms"
        )
        
    bridge.disconnect()
    print(np.mean(t_arr))
    print(np.std(t_arr))


if __name__ == "__main__":
    main()
