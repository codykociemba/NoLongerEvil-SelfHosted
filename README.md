# NoLongerEvil Self-Hosted Server

[![Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?logo=discord&logoColor=white)](https://discord.gg/hackhouse)
[![codecov](https://codecov.io/gh/codykociemba/NoLongerEvil-SelfHosted/graph/badge.svg)](https://codecov.io/gh/codykociemba/NoLongerEvil-SelfHosted)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/codykociemba/NoLongerEvil-SelfHosted/actions/workflows/ci.yml/badge.svg)](https://github.com/codykociemba/NoLongerEvil-SelfHosted/actions/workflows/ci.yml)
[![GitHub Release](https://img.shields.io/github/v/release/codykociemba/NoLongerEvil-SelfHosted)](https://github.com/codykociemba/NoLongerEvil-SelfHosted/releases/latest)

A self-hosted server implementation for Nest thermostats, written in Python. This server emulates Nest cloud endpoints, allowing you to maintain control of your Nest thermostat locally without relying on external cloud services.

## Features

- **Full Nest Protocol Support**: Emulates Nest cloud API endpoints for seamless device communication
- **Dual-Port Architecture**: Separate APIs for thermostat communication and dashboard/automation
- **Long-Polling Subscriptions**: Real-time device state updates without constant polling
- **Temperature Safety Bounds**: Configurable min/max temperature limits to prevent extreme settings
- **Device Availability Tracking**: Monitor device connectivity with automatic timeout detection
- **Weather Service**: Proxied weather data with caching to reduce API calls
- **MQTT Integration**: Publish device state to MQTT brokers for Home Assistant integration
- **Home Assistant Auto-Discovery**: Automatic device discovery in Home Assistant via MQTT
- **API Key Authentication**: Secure control API access with API keys
- **Device Sharing**: Share device access with other users
- **Persistent Storage**: SQLite3 database for reliable state persistence
- **Docker Support**: Easy deployment with Docker and Docker Compose

## Quick Start

### Using Docker Compose (Recommended)

1. Clone the repository:
   ```bash
   git clone https://github.com/codykociemba/NoLongerEvil-SelfHosted.git
   cd nolongerevil-selfhosted
   ```

2. Create your configuration:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

3. Start the server:
   ```bash
   docker compose up -d
   ```

The server will be available at:
- **Device API**: Port 7001 (HTTP) or 443 (HTTPS)
- **Control API**: Port 8081

### Using Python Directly

Requires Python 3.11 or higher.

1. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

2. Install the package (uses `pyproject.toml` for dependencies):
   ```bash
   pip install .
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   ```

4. Run the server:
   ```bash
   nolongerevil-server
   # Or: python -m nolongerevil.main
   ```

## Configuration

Configuration is done via environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_ORIGIN` | `http://localhost` | Base URL for thermostat connections |
| `SERVER_PORT` | `443` | Port for thermostat connections |
| `CONTROL_PORT` | `8081` | Port for control API |
| `CERT_DIR` | - | Directory containing TLS certificates |
| `ENTRY_KEY_TTL_SECONDS` | `3600` | Pairing code expiration (seconds) |
| `WEATHER_CACHE_TTL_MS` | `600000` | Weather cache duration (ms) |
| `MAX_SUBSCRIPTIONS_PER_DEVICE` | `100` | Max concurrent subscriptions |
| `SUSPEND_TIME_MAX` | `600` | Device sleep duration before fallback wake (seconds) |
| `DEFER_DEVICE_WINDOW` | `15` | Delay before device sends updates after local changes (seconds) |
| `DEBUG_LOGGING` | `false` | Enable debug logging |
| `SQLITE3_DB_PATH` | `./data/database.sqlite` | Database file path |

### MQTT Configuration (Optional)

To enable MQTT integration for Home Assistant:

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_HOST` | - | MQTT broker hostname (required to enable MQTT) |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USER` | - | MQTT username (optional) |
| `MQTT_PASSWORD` | - | MQTT password (optional) |
| `MQTT_TOPIC_PREFIX` | `nolongerevil` | Prefix for MQTT topics |
| `MQTT_DISCOVERY_PREFIX` | `homeassistant` | Home Assistant discovery prefix |

## API Reference

### Device API (Server Port)

These endpoints emulate Nest cloud services:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/nest/entry` | GET | Service discovery |
| `/nest/ping` | GET | Health check |
| `/nest/passphrase` | GET | Generate pairing code |
| `/nest/transport` | POST | Subscribe to device updates |
| `/nest/transport/put` | POST | Push device state updates |
| `/nest/transport/device/{serial}` | GET | Get device objects |
| `/nest/weather/v1` | GET | Weather data proxy |

### Control API (Control Port)

These endpoints are for dashboards and automation:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/command` | POST | Send commands to thermostat |
| `/status` | GET | Get device status |
| `/api/devices` | GET | List all devices |
| `/api/stats` | GET | Server statistics |
| `/notify-device` | POST | Force notification to subscribers |
| `/health` | GET | Health check |

#### Command Examples

**Set Temperature:**
```bash
curl -X POST http://localhost:8081/command \
  -H "Content-Type: application/json" \
  -d '{"serial": "YOUR_SERIAL", "command": "set_temperature", "value": 21.5}'
```

**Set Mode:**
```bash
curl -X POST http://localhost:8081/command \
  -H "Content-Type: application/json" \
  -d '{"serial": "YOUR_SERIAL", "command": "set_mode", "value": "heat"}'
```

**Set Away Mode:**
```bash
curl -X POST http://localhost:8081/command \
  -H "Content-Type: application/json" \
  -d '{"serial": "YOUR_SERIAL", "command": "set_away", "value": true}'
```

**Set Fan:**
```bash
curl -X POST http://localhost:8081/command \
  -H "Content-Type: application/json" \
  -d '{"serial": "YOUR_SERIAL", "command": "set_fan", "value": "on"}'
```

## Home Assistant Integration

### Via MQTT Auto-Discovery

1. Enable MQTT integration in your configuration
2. The server will automatically publish Home Assistant discovery messages
3. Devices will appear in Home Assistant under the "Climate" integration

### Manual Configuration

If you prefer manual configuration, add to your `configuration.yaml`:

```yaml
climate:
  - platform: mqtt
    name: "Nest Thermostat"
    current_temperature_topic: "nolongerevil/YOUR_SERIAL/device/current_temperature"
    temperature_command_topic: "nolongerevil/YOUR_SERIAL/device/target_temperature/set"
    temperature_state_topic: "nolongerevil/YOUR_SERIAL/device/target_temperature"
    mode_command_topic: "nolongerevil/YOUR_SERIAL/device/mode/set"
    mode_state_topic: "nolongerevil/YOUR_SERIAL/device/mode"
    modes:
      - "off"
      - "heat"
      - "cool"
      - "heat_cool"
```

## Deployment

### Docker

Build and run the Docker image:

```bash
docker build -t nolongerevil-server .
docker run -d \
  -p 7001:80 \
  -p 8081:8081 \
  -v nolongerevil-data:/app/data \
  nolongerevil-server
```

### TLS/HTTPS

For production deployments with HTTPS:

1. Place your certificates in a directory:
   ```
   certs/
   ├── fullchain.pem
   └── privkey.pem
   ```

2. Configure the server:
   ```bash
   CERT_DIR=/path/to/certs
   SERVER_PORT=443
   ```

3. Mount the certificates in Docker:
   ```yaml
   volumes:
     - ./certs:/app/certs:ro
   environment:
     - CERT_DIR=/app/certs
   ```

## Contributing

See the [CONTRIBUTING](CONTRIBUTING.md) guide for development setup instructions.

## License

MIT License - see [LICENSE](LICENSE) for details.
