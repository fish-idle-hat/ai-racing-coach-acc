#!/usr/bin/env python3
import argparse
import json
import math
import socket
import time


def main():
    parser = argparse.ArgumentParser(description="Send fake ACC telemetry packets for receiver testing")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47777)
    parser.add_argument("--hz", type=float, default=20.0)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--track", default="simulated_track")
    parser.add_argument("--lap-seconds", type=float, default=120.0)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.hz
    start = time.time()
    packet_id = 0

    while time.time() - start < args.seconds:
        t = time.time() - start
        speed = max(0.0, 115 + 70 * math.sin(t * 0.55))
        normalized_position = (t % args.lap_seconds) / args.lap_seconds
        distance_traveled = 7004 * normalized_position
        braking_phase = (
            normalized_position < 0.08
            or 0.27 <= normalized_position < 0.34
            or 0.40 <= normalized_position < 0.47
            or 0.90 <= normalized_position < 0.97
        )
        packet = {
            "source": "fake_sender",
            "packet_id": packet_id,
            "speed_kmh": round(speed, 3),
            "throttle": 0.0 if braking_phase else round(max(0.0, math.sin(t * 0.9)), 3),
            "brake": 0.75 if braking_phase else 0.0,
            "steer": round(0.35 * math.sin(t * 1.8), 3),
            "gear": int(max(1, min(6, speed // 40 + 1))),
            "rpm": int(3500 + 2500 * abs(math.sin(t * 1.2))),
            "lap_time_ms": int(t * 1000) % 120000,
            "last_lap_ms": 108500,
            "best_lap_ms": 107850,
            "lap_count": int(t // args.lap_seconds),
            "normalized_car_position": normalized_position,
            "distance_traveled": distance_traveled,
            "current_sector_index": int(normalized_position * 3),
            "track": args.track,
            "car_model": "simulated_gt3",
        }
        sock.sendto(json.dumps(packet).encode("utf-8"), (args.host, args.port))
        packet_id += 1
        time.sleep(interval)

    print(f"Sent {packet_id} fake telemetry packets to udp://{args.host}:{args.port}")


if __name__ == "__main__":
    main()
