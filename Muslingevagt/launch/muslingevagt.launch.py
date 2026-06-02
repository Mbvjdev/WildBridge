"""
Muslingevagt + BirdTracker ROS 2 launch file.
Based on WildBridge's wildview_bringup pattern.

Launches:
  - birdtracker_node    (detection publisher)
  - Optional: MAVROS bridge for boat control
"""

import os
from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "boat_ip", default_value="192.168.1.50",
            description="Muslingevagt boat companion computer IP"),
        DeclareLaunchArgument(
            "boat_mavlink_port", default_value="14550",
            description="MAVLink UDP port on boat"),
        DeclareLaunchArgument(
            "telemetry_port", default_value="8081",
            description="TCP telemetry port (WildBridge format)"),
        DeclareLaunchArgument(
            "publish_rate", default_value="10.0",
            description="Detection publish rate (Hz)"),
        DeclareLaunchArgument(
            "boat_lat", default_value="55.4761",
            description="Initial boat latitude"),
        DeclareLaunchArgument(
            "boat_lon", default_value="10.5370",
            description="Initial boat longitude"),

        # BirdTracker detection node
        Node(
            package="muslingevagt",
            executable="birdtracker_node",
            name="birdtracker",
            output="screen",
            parameters=[{
                "publish_rate_hz": LaunchConfiguration("publish_rate"),
                "telemetry_tcp_port": LaunchConfiguration("telemetry_port"),
                "boat_lat": LaunchConfiguration("boat_lat"),
                "boat_lon": LaunchConfiguration("boat_lon"),
            }],
        ),
    ])
