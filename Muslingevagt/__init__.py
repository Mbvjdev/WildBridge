# Muslingevagt + BirdTracker — ROS 2 integration for WildBridge
# Runs on: RPi 5 (BirdTracker) + boat companion computer + ground station Mac

from .birdtracker.birdtracker_node import BirdTrackerNode, main

__all__ = ["BirdTrackerNode", "main"]
