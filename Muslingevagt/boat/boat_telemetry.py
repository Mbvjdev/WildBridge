#!/usr/bin/env python3
"""
boat_telemetry.py — WildBridge-compatible TCP JSON telemetry for Muslingevagt USV.
Pattern: Mirrors WildBridge's Android telemetry stream (port 8081).
Runs on the boat's companion computer, reading MAVLink telemetry.

Format: Newline-delimited JSON, same shape as WildBridge drone telemetry.
Connect:  nc <boat-ip> 8081

Author: Cille for WildBridge/Muslingevagt
License: MIT
"""

import json
import socket
import struct
import threading
import time
from typing import Optional

# ---------------------------------------------------------------------------
# MAVLink parser (lightweight, no pymavlink dependency)
# ---------------------------------------------------------------------------
class MAVLinkMini:
    """Parse key MAVLink v2 messages from a binary stream."""

    # MAVLink v2 header: STX(1) LEN(1) INC(1) CMP(1) SEQ(1) SYS(1) COMP(1) MSGID(3)
    HEADER_FMT = "<BBBBBB3s"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)

    GLOBAL_POSITION_INT = 33
    ATTITUDE = 30
    BATTERY_STATUS = 147
    GPS_RAW_INT = 24
    VFR_HUD = 74
    HEARTBEAT = 0

    @staticmethod
    def parse_heartbeat(payload: bytes) -> dict:
        t, autopilot, base_mode, custom_mode, system_status, mavlink_version = \
            struct.unpack("<BBBBBB", payload[:6])
        mode_map = {0: "MANUAL", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL"}
        return {"flight_mode": mode_map.get(custom_mode, f"MODE_{custom_mode}")}

    @staticmethod
    def parse_global_position(payload: bytes) -> dict:
        lat, lon, alt, rel_alt, vx, vy, vz, hdg = \
            struct.unpack("<iiiiHHHH", payload[:28]) if len(payload) >= 28 else (0,)*8
        return {
            "lat": lat / 1e7,
            "lon": lon / 1e7,
            "alt": alt / 1000.0,
            "rel_alt": rel_alt / 1000.0,
            "heading": hdg / 100.0,
        }

    @staticmethod
    def parse_battery(payload: bytes) -> dict:
        if len(payload) < 10:
            return {"battery": 0}
        _, _, _, _, _, _, _, _, _, remaining = struct.unpack("<HHBBBBBBBB", payload[:10])
        return {"battery": remaining}


# ---------------------------------------------------------------------------
# Telemetry Stream Server
# ---------------------------------------------------------------------------
class BoatTelemetryStream:
    """WildBridge-compatible TCP JSON telemetry on port 8081.

    Reads MAVLink from ArduPilot flight controller and streams
    structured JSON to all connected ground station clients.
    """

    def __init__(
        self,
        mavlink_port: int = 14550,
        telemetry_port: int = 8081,
        host: str = "0.0.0.0",
        boat_name: str = "Muslingevagt",
    ):
        self.mavlink_port = mavlink_port
        self.telemetry_port = telemetry_port
        self.host = host
        self.boat_name = boat_name
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        self.running = True

        # Start MAVLink reader thread
        self._mavlink_thread = threading.Thread(
            target=self._mavlink_loop, daemon=True)
        self._mavlink_thread.start()

        # Start TCP server
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.telemetry_port))
        self._server.listen(5)
        self._server.settimeout(1.0)
        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True)
        self._accept_thread.start()

        print(f"[boat_tele] TCP telemetry on :{self.telemetry_port}")

    def _accept_loop(self):
        while self.running:
            try:
                client, addr = self._server.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self.lock:
                    self.clients.append(client)
                print(f"[boat_tele] client connected: {addr}")
            except socket.timeout:
                continue
            except Exception:
                break

    def _mavlink_loop(self):
        """Parse MAVLink UDP stream from ArduPilot."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.mavlink_port))
        except OSError:
            print(f"[boat_tele] WARNING: could not bind MAVLink port {self.mavlink_port}")
            print(f"[boat_tele] Will stream zeros — connect MAVLink source!")

        sock.settimeout(0.5)

        # State
        lat, lon, alt, heading = 0.0, 0.0, 0.0, 0.0
        battery = 100
        flight_mode = "MANUAL"
        home_lat, home_lon = 0.0, 0.0

        last_broadcast = 0.0
        BROADCAST_RATE = 10.0  # Hz

        while self.running:
            try:
                data, _ = sock.recvfrom(4096)
                # Parse MAVLink v2 messages
                pos = 0
                while pos + MAVLinkMini.HEADER_SIZE <= len(data):
                    hdr = struct.unpack(
                        MAVLinkMini.HEADER_FMT,
                        data[pos:pos + MAVLinkMini.HEADER_SIZE])
                    stx, payload_len, inc, cmp, seq, sysid, compid, msgid_bytes = hdr
                    msgid = struct.unpack("<I", msgid_bytes + b"\x00")[0] & 0xFFFFFF

                    payload_start = pos + MAVLinkMini.HEADER_SIZE
                    payload_end = payload_start + payload_len

                    if payload_end > len(data):
                        break

                    payload = data[payload_start:payload_end]

                    if msgid == MAVLinkMini.GLOBAL_POSITION_INT:
                        gp = MAVLinkMini.parse_global_position(payload)
                        lat, lon, alt, heading = (
                            gp["lat"], gp["lon"], gp["alt"], gp["heading"])
                    elif msgid == MAVLinkMini.BATTERY_STATUS:
                        battery = MAVLinkMini.parse_battery(payload)["battery"]
                    elif msgid == MAVLinkMini.HEARTBEAT:
                        fm = MAVLinkMini.parse_heartbeat(payload)
                        flight_mode = fm["flight_mode"]

                    pos = payload_end + 2  # +2 for MAVLink v2 checksum

            except socket.timeout:
                pass

            # Broadcast at fixed rate
            now = time.time()
            if now - last_broadcast >= 1.0 / BROADCAST_RATE:
                self._broadcast({
                    "boatName": self.boat_name,
                    "location": {
                        "latitude": lat,
                        "longitude": lon,
                        "altitude": alt,
                    },
                    "heading": heading,
                    "speed": 0.0,  # TODO: parse from GPS_RAW_INT
                    "batteryLevel": int(battery),
                    "satelliteCount": 0,
                    "flightMode": flight_mode,
                    "homeLocation": {
                        "latitude": home_lat,
                        "longitude": home_lon,
                    },
                    "remainingFlightTime": -1,  # boats don't have this
                    "timestamp": now,
                })
                last_broadcast = now

    def _broadcast(self, data: dict):
        line = (json.dumps(data) + "\n").encode()
        with self.lock:
            dead = []
            for client in self.clients:
                try:
                    client.sendall(line)
                except Exception:
                    dead.append(client)
            for client in dead:
                self.clients.remove(client)

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Muslingevagt TCP telemetry")
    parser.add_argument("--mavlink-port", type=int, default=14550)
    parser.add_argument("--telemetry-port", type=int, default=8081)
    parser.add_argument("--name", default="Muslingevagt")
    args = parser.parse_args()

    stream = BoatTelemetryStream(
        mavlink_port=args.mavlink_port,
        telemetry_port=args.telemetry_port,
        boat_name=args.name,
    )
    stream.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()


if __name__ == "__main__":
    main()
