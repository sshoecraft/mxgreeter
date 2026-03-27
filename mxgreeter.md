# mxgreeter -- Auto-Join Bot for Matrix/Synapse

## Overview
A matrix-nio bot that ensures all server users are members of configured rooms. When a user logs in (detected via presence events), the bot checks their room membership and force-joins them to any required rooms they're missing using the Synapse Admin API. Auto-registers on first run (same flow as mxai).

## Architecture

### Two-Layer Detection
1. **Presence events (primary)** -- Event-driven. The bot receives `PresenceEvent` callbacks via matrix-nio's `/sync`. When a user transitions to `online`, the bot checks and enforces room membership. A 5-minute cooldown per user prevents redundant API calls.

2. **Periodic polling (safety net)** -- Every `poll_interval` seconds, the bot queries `/_synapse/admin/v2/users` for all local users and checks each one. This catches users whose presence events were missed (e.g., the bot was restarting).

### Authentication
On first run, the bot auto-registers via Synapse's dummy auth flow (same as mxai). Credentials are saved to `~/.config/mxgreeter/credentials/` and reused on subsequent runs. Optionally, a separate admin user can be configured for Admin API calls.

### Force-Join via Admin API
Uses `POST /_synapse/admin/v1/join/{room}` with `{"user_id": "..."}` to join users directly -- no invite acceptance required. The bot user (or configured admin user) must be a Synapse server admin.

### Admin API Endpoints Used
- `GET /_synapse/admin/v2/users` -- list all local users
- `GET /_synapse/admin/v1/users/{user_id}/joined_rooms` -- check user's current rooms
- `POST /_synapse/admin/v1/join/{room}` -- force-join a user to a room

## Files
- `mxgreeter/__init__.py` -- Package init, version
- `mxgreeter/__main__.py` -- Entry point for `python3 -m mxgreeter`
- `mxgreeter/bot.py` -- Core `Greeter` class with presence handling and admin API calls
- `mxgreeter/cli.py` -- CLI argument parsing and config loading
- `config.toml` -- Default configuration
- `pyproject.toml` -- Package metadata

## Configuration
TOML config file with:
- `server` -- Synapse homeserver URL
- `server_name` -- Matrix server name (e.g., "mckesson")
- `username` / `password` -- Bot user credentials (auto-registers on first run)
- `rooms` -- List of room aliases to enforce membership on
- `admin_user` / `admin_password` -- Optional separate admin account for API calls
- `poll_interval` -- Seconds between full user polls
- `verbose` -- Enable detailed logging

## Prerequisites
- Synapse must have registration enabled (or bot must be pre-registered)
- Bot user (or configured admin_user) must be a **Synapse server admin**
- `matrix-nio` and `aiohttp` must be installed

## Usage
```bash
# Via config file
python3 -m mxgreeter --config config.toml

# Via CLI args
python3 -m mxgreeter -s http://localhost:8008 -u mxgreeter -p mxgreeter --server-name mckesson -r General
```

## Version History
- **0.2.0** -- Auto-registration, saved credentials, optional separate admin user
- **0.1.0** -- Initial implementation with presence monitoring + admin API polling
