#!/usr/bin/env python3
"""
boat_http_api.py — WildBridge-compatible HTTP command API for Muslingevagt USV.
Runs on the boat's companion computer (RPi/Orin).
Pattern: Mirrors WildBridge's Android HTTP server (port 8080).

Endpoints:
  POST /send/activateDeterrence    → enable bird deterrence
  POST /send/deactivateDeterrence  → disable bird deterrence
  POST /send/gotoWP                → navigate to waypoint (lat,lon)
  POST /send/RTH                   → return to home
  POST /send/abortMission          → stop current mission
  GET  /config                     → boat status & config
  GET  /telemetry                  → current telemetry snapshot

Author: Cille for WildBridge/Muslingevagt
License: MIT
"""

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

# ---------------------------------------------------------------------------
# Boat state (shared between API and telemetry)
# ---------------------------------------------------------------------------
class BoatState:
    def __init__(self):
        self.lat: float = 0.0
        self.lon: float = 0.0
        self.heading: float = 0.0
        self.speed: float = 0.0
        self.battery: float = 0.0
        self.deterrence_active: bool = False
        self.mission_active: bool = False
        self.home_lat: float = 0.0
        self.home_lon: float = 0.0
        self.flight_mode: str = "MANUAL"  # WildBridge naming
        self.satellites: int = 0
        self.last_command: str = ""
        self.last_command_time: float = 0.0
        self.uptime_s: float = 0.0
        self.start_time: float = time.time()
        self.lock = threading.Lock()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "boatName": "Muslingevagt",
                "location": {
                    "latitude": self.lat,
                    "longitude": self.lon,
                },
                "heading": self.heading,
                "speed": self.speed,
                "batteryLevel": int(self.battery),
                "deterrenceActive": self.deterrence_active,
                "missionActive": self.mission_active,
                "homeLocation": {
                    "latitude": self.home_lat,
                    "longitude": self.home_lon,
                },
                "flightMode": self.flight_mode,
                "satelliteCount": self.satellites,
                "lastCommand": self.last_command,
                "lastCommandTime": self.last_command_time,
                "uptime": time.time() - self.start_time,
                "timestamp": time.time(),
            }


state = BoatState()


# ---------------------------------------------------------------------------
# Command handlers (override these with actual hardware calls)
# ---------------------------------------------------------------------------
def activate_deterrence():
    """Enable bird deterrence (laser/sound)."""
    state.deterrence_active = True
    state.last_command = "activate_deterrence"
    state.last_command_time = time.time()
    print("[boat_api] DETERRENCE ACTIVATED")
    # TODO: GPIO/laser/speaker activation
    return {"status": "ok", "deterrence": "active"}


def deactivate_deterrence():
    """Disable bird deterrence."""
    state.deterrence_active = False
    state.last_command = "deactivate_deterrence"
    state.last_command_time = time.time()
    print("[boat_api] DETERRENCE DEACTIVATED")
    return {"status": "ok", "deterrence": "inactive"}


def goto_waypoint(lat: float, lon: float):
    """Send waypoint to ArduPilot via MAVLink."""
    state.mission_active = True
    state.last_command = f"gotoWP({lat}, {lon})"
    state.last_command_time = time.time()
    print(f"[boat_api] GOTO: {lat}, {lon}")
    # TODO: Send MAVLink WAYPOINT or SET_POSITION_TARGET_GLOBAL_INT
    return {"status": "ok", "waypoint": {"lat": lat, "lon": lon}}


def return_to_home():
    """RTH — abort mission and return to home position."""
    state.mission_active = False
    state.last_command = "RTH"
    state.last_command_time = time.time()
    print(f"[boat_api] RTH → home ({state.home_lat}, {state.home_lon})")
    return {"status": "ok", "home": {"lat": state.home_lat, "lon": state.home_lon}}


def abort_mission():
    """Stop current mission."""
    state.mission_active = False
    state.last_command = "abort"
    state.last_command_time = time.time()
    print("[boat_api] MISSION ABORTED")
    return {"status": "ok", "mission": "aborted"}


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class BoatHTTPHandler(BaseHTTPRequestHandler):
    """WildBridge-compatible HTTP API on port 8080."""

    def log_message(self, format, *args):
        print(f"[boat_api] {args[0]}")

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode() if length > 0 else ""

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/config":
            self._send_json({
                "boatName": "Muslingevagt",
                "ip": "192.168.1.50",      # TODO: detect actual IP
                "httpPort": 8080,
                "telemetryPort": 8081,
                "version": "1.0.0-muslingevagt",
                "hardware": "RPi5 + ArduPilot",
            })
        elif path == "/telemetry":
            self._send_json(state.snapshot())
        elif path == "/get/deterrence":
            self._send_text(
                "active" if state.deterrence_active else "inactive")
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()
        params = {}

        # Parse comma-separated body (WildBridge format)
        if body:
            parts = [p.strip() for p in body.split(",")]
            keys = ["lat", "lon", "alt", "yaw", "speed"]
            for i, val in enumerate(parts):
                if i < len(keys):
                    params[keys[i]] = float(val) if val else 0.0

        try:
            if path == "/send/activateDeterrence":
                result = activate_deterrence()
            elif path == "/send/deactivateDeterrence":
                result = deactivate_deterrence()
            elif path == "/send/gotoWP":
                lat = params.get("lat", state.lat)
                lon = params.get("lon", state.lon)
                result = goto_waypoint(lat, lon)
            elif path == "/send/RTH":
                result = return_to_home()
            elif path == "/send/abortMission":
                result = abort_mission()
            else:
                self._send_json({"error": f"unknown endpoint: {path}"}, 404)
                return

            self._send_json(result)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Muslingevagt HTTP API")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--home-lat", type=float, default=55.4761)
    parser.add_argument("--home-lon", type=float, default=10.5370)
    args = parser.parse_args()

    state.home_lat = args.home_lat
    state.home_lon = args.home_lon

    server = HTTPServer(("0.0.0.0", args.port), BoatHTTPHandler)
    print(f"[boat_api] WildBridge HTTP API on :{args.port}")
    print(f"[boat_api] Home: {args.home_lat}, {args.home_lon}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
