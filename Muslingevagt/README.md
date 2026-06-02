# Muslingevagt + BirdTracker — WildBridge Integration

Autonom muslingefarm-overvågning med AI-drevet fugledetektion og deterrence.

```
BirdTracker (RPi 5 + Hailo-8L)          Muslingevagt USV (ArduPilot)
         │                                         │
         ├─ TCP JSON :8081 ──────────────────────→ │  WildBridge telemetri format
         ├─ ROS 2 /birdtracker/detections ────────→ │  GeoPoseStamped
         └─ HTTP POST :8080/send/gotoWP ──────────→ │  Waypoint kommandoer
                                                    │
         ┌──────────────────────────────────────────┘
         ▼
    Ground Station (Mac mini / laptop)
    ├─ ROS 2 Humble + CycloneDDS
    ├─ Dashboard (positionskort + video)
    └─ MAVROS bridge → båd waypoints
```

## Struktur

```
Muslingevagt/
├── birdtracker/
│   └── birdtracker_node.py    # ROS 2 detection publisher + TCP telemetri
├── boat/
│   ├── boat_http_api.py       # HTTP command API (:8080)
│   └── boat_telemetry.py      # MAVLink → TCP JSON telemetri (:8081)
├── ground_station/
│   ├── docker-compose.yaml    # MediaMTX + dashboard
│   └── dashboard/             # Browser UI
├── launch/
│   └── muslingevagt.launch.py # ROS 2 launch
├── config/
│   └── parameters.yaml        # Fælles konfiguration
├── requirements.txt
└── README.md
```

## Quick Start

### 1. På båden (companion computer)

```bash
# Start HTTP command API (port 8080)
python3 Muslingevagt/boat/boat_http_api.py --home-lat 55.4761 --home-lon 10.5370 &

# Start TCP telemetri (port 8081, læser MAVLink fra ArduPilot)
python3 Muslingevagt/boat/boat_telemetry.py --mavlink-port 14550 &
```

Test:
```bash
curl http://192.168.1.50:8080/config
nc 192.168.1.50 8081   # live telemetri
curl -X POST http://192.168.1.50:8080/send/activateDeterrence
```

### 2. På RPi 5 (BirdTracker)

```bash
# Start ROS 2 detection node
ros2 run muslingevagt birdtracker_node --ros-args \
  -p boat_lat:=55.4761 -p boat_lon:=10.5370

# Eller via launch
ros2 launch muslingevagt muslingevagt.launch.py
```

Test:
```bash
nc 192.168.1.50 8081          # se detektioner
ros2 topic echo /birdtracker/detections   # se ROS topics
```

### 3. Ground Station (Mac mini / laptop)

```bash
cd Muslingevagt/ground_station
docker compose up -d
# Dashboard: http://localhost:8090
```

## API Reference

### TCP Telemetri (:8081) — WildBridge format

```json
{
  "boatName": "Muslingevagt",
  "location": {"latitude": 55.4761, "longitude": 10.5370, "altitude": 0.0},
  "heading": 45.0,
  "batteryLevel": 85,
  "flightMode": "GUIDED",
  "timestamp": 1717324800.0
}
```

### HTTP Commands (:8080) — WildBridge format

| Endpoint | Beskrivelse |
|---|---|
| `GET /config` | Bådstatus og konfiguration |
| `GET /telemetry` | Aktuelt telemetri-snapshot |
| `POST /send/activateDeterrence` | Aktiver fugleskræm |
| `POST /send/deactivateDeterrence` | Deaktiver fugleskræm |
| `POST /send/gotoWP` | Body: `lat,lon` — sejl til waypoint |
| `POST /send/RTH` | Return to home |
| `POST /send/abortMission` | Stop mission |

### ROS 2 Topics (BirdTracker)

| Topic | Type | Beskrivelse |
|---|---|---|
| `/birdtracker/detections` | GeoPoseStamped | Fuglepositioner (10 Hz) |
| `/birdtracker/status` | String (JSON) | FPS, temp, batteri, detections/min |
| `/birdtracker/image` | CompressedImage | Annotated frame (valgfri) |

## Konfiguration

`config/parameters.yaml`:
```yaml
birdtracker:
  inference_udp_port: 5000
  telemetry_tcp_port: 8081
  publish_rate_hz: 10.0
  camera_fov_h: 62.2
  camera_fov_v: 48.8

boat:
  http_port: 8080
  telemetry_port: 8081
  mavlink_port: 14550
  home_lat: 55.4761
  home_lon: 10.5370
```

## License

MIT — del af [WildBridge](https://github.com/WildDrone/WildBridge) projektet.
