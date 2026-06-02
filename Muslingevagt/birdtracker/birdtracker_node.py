#!/usr/bin/env python3
"""
birdtracker_node.py — ROS 2 detection publisher for BirdTracker v3.
Pattern: Based on WildBridge's dji_controller node.
Runs on RPi 5 with Hailo-8L AI processor.

Published topics:
  /birdtracker/detections  (GeoPoseStamped) — bird positions
  /birdtracker/status      (String) — FPS, temp, battery, health
  /birdtracker/image       (CompressedImage) — annotated frame (optional)

Author: Cille for WildBridge/Muslingevagt
License: MIT
"""

import json
import time
import threading
import socket
import struct
from pathlib import Path

import rclpy
from rclpy.node import Node
from geographic_msgs.msg import GeoPoseStamped
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

# --- WildBridge-compatible TCP JSON telemetry stream ---
# Format: newline-delimited JSON on port 8081
# This is the SAME format WildBridge uses for drone telemetry.
# Any WildBridge-compatible ground station can consume this.

class JSONTelemetryStream:
    """WildBridge-compatible TCP JSON telemetry on port 8081."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8081):
        self.host = host
        self.port = port
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()
        self.running = False
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(5)
        self._server.settimeout(1.0)
        self.running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while self.running:
            try:
                client, addr = self._server.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self.lock:
                    self.clients.append(client)
            except socket.timeout:
                continue
            except Exception:
                break

    def broadcast(self, data: dict):
        """Send JSON line to all connected clients (non-blocking)."""
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
        if self._thread:
            self._thread.join(timeout=2)


class BirdTrackerNode(Node):
    """ROS 2 node for BirdTracker v3 detection publishing.

    Reads from the Hailo-8L inference pipeline (via shared memory or UDP)
    and publishes standardized ROS 2 topics + WildBridge TCP telemetry.
    """

    def __init__(self):
        super().__init__("birdtracker")

        # --- Publishers ---
        self.detection_pub = self.create_publisher(
            GeoPoseStamped, "birdtracker/detections", 10)
        self.status_pub = self.create_publisher(
            String, "birdtracker/status", 10)
        self.image_pub = self.create_publisher(
            CompressedImage, "birdtracker/image", 5)

        # --- Parameters ---
        self.declare_parameter("inference_udp_port", 5000)
        self.declare_parameter("telemetry_tcp_port", 8081)
        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("boat_lat", 0.0)   # current boat position
        self.declare_parameter("boat_lon", 0.0)
        self.declare_parameter("camera_fov_h", 62.2)  # degrees
        self.declare_parameter("camera_fov_v", 48.8)

        # --- TCP telemetry stream (WildBridge format) ---
        tcp_port = self.get_parameter("telemetry_tcp_port").value
        self.telemetry = JSONTelemetryStream(port=tcp_port)
        self.telemetry.start()
        self.get_logger().info(f"TCP telemetry on :{tcp_port}")

        # --- Timer ---
        rate = self.get_parameter("publish_rate_hz").value
        self.timer = self.create_timer(1.0 / rate, self._publish_loop)

        # --- State ---
        self._detections: list[dict] = []
        self._frame_id = 0
        self._fps = 0.0
        self._hailo_temp = 0.0
        self._battery = 0.0
        self._start_time = time.time()

        self.get_logger().info("BirdTracker node ready")

    # ------------------------------------------------------------------
    # Public API — called from inference pipeline
    # ------------------------------------------------------------------

    def update_detections(self, detections: list[dict]):
        """Update the current detection list.

        Args:
            detections: List of dicts with keys:
                lat, lon, confidence, class_name, bbox (x,y,w,h)
        """
        self._detections = detections
        self._frame_id += 1

    def update_health(self, fps: float, hailo_temp: float, battery: float):
        self._fps = fps
        self._hailo_temp = hailo_temp
        self._battery = battery

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _publish_loop(self):
        now = time.time()
        boat_lat = self.get_parameter("boat_lat").value
        boat_lon = self.get_parameter("boat_lon").value

        # --- Publish each detection as GeoPoseStamped ---
        for det in self._detections:
            msg = GeoPoseStamped()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = f"birdtracker_{self._frame_id}"
            msg.pose.position.latitude = det.get("lat", boat_lat)
            msg.pose.position.longitude = det.get("lon", boat_lon)
            msg.pose.position.altitude = 0.0  # sea level

            # Store confidence and class in the orientation quaternion (hack)
            msg.pose.orientation.x = float(det.get("confidence", 0.0))
            msg.pose.orientation.y = float(hash(det.get("class_name", "bird")) % 255)
            msg.pose.orientation.z = 0.0
            msg.pose.orientation.w = 1.0

            self.detection_pub.publish(msg)

        # --- Status ---
        status = {
            "timestamp": now,
            "uptime_s": now - self._start_time,
            "fps": self._fps,
            "hailo_temp_c": self._hailo_temp,
            "battery_pct": self._battery,
            "detections": len(self._detections),
            "frame_id": self._frame_id,
            "boat_position": {"lat": boat_lat, "lon": boat_lon},
        }
        self.status_pub.publish(String(data=json.dumps(status)))

        # --- TCP telemetry (WildBridge format) ---
        # Mirrors WildBridge's drone telemetry JSON shape
        telemetry = {
            "droneName": "BirdTracker",
            "timestamp": now,
            "detections": [
                {
                    "lat": d.get("lat", boat_lat),
                    "lon": d.get("lon", boat_lon),
                    "confidence": d.get("confidence", 0.0),
                    "class": d.get("class_name", "bird"),
                }
                for d in self._detections
            ],
            "detection_count": len(self._detections),
            "fps": self._fps,
            "hailo_temp": self._hailo_temp,
            "battery_level": int(self._battery),
            "boat_known_position": {"lat": boat_lat, "lon": boat_lon},
        }
        self.telemetry.broadcast(telemetry)

    def destroy_node(self):
        self.telemetry.stop()
        super().destroy_node()


# ------------------------------------------------------------------
# Standalone entry point
# ------------------------------------------------------------------
def main(args=None):
    rclpy.init(args=args)
    node = BirdTrackerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
