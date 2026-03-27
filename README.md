# mxgreeter

Auto-join bot for Matrix/Synapse that ensures all server users are members of configured rooms.

## Why

In a self-hosted Synapse environment, new users don't automatically join any rooms after registration. They land in an empty client with no idea where to go. mxgreeter solves this by monitoring user presence and force-joining them into the rooms they should be in -- no manual invites, no user action required.

## How It Works

mxgreeter uses two complementary mechanisms to catch every user:

1. **Presence events** -- When a user comes online, the bot detects it via Matrix `/sync` and checks their room membership. If they're missing from any required room, it force-joins them using the Synapse Admin API. A 5-minute cooldown prevents redundant API calls.

2. **Periodic polling** -- Every `poll_interval` seconds, the bot queries the Synapse Admin API for all local users and verifies each one. This catches anyone whose presence event was missed (e.g., if the bot was restarting).

Force-joins are done via `POST /_synapse/admin/v1/join/{room}` so users are added directly -- no invite acceptance needed.

## Prerequisites

- A running Synapse homeserver
- The bot user (or a configured admin user) must be a **Synapse server admin**
- Python 3.11+

## Installation

```bash
pip install .
```

## Configuration

Create a TOML config file (see `config.toml` for an example):

```toml
server = "http://localhost:8008"
server_name = "example"
username = "greeter"
password = "greeter"

rooms = [
    "#General:example",
]

# Optional: use a separate admin account for Admin API calls
admin_user = "admin"
admin_password = "admin"

# Seconds between full user list polls
poll_interval = 60

verbose = true
```

| Key              | Description                                              |
|------------------|----------------------------------------------------------|
| server           | Synapse homeserver URL                                   |
| server_name      | Matrix server name (the part after `:` in user IDs)      |
| username         | Bot username (auto-registers on first run)                |
| password         | Bot password                                             |
| rooms            | List of room aliases to enforce membership on             |
| admin_user       | Optional separate admin account for API calls             |
| admin_password   | Password for the admin account                           |
| poll_interval    | Seconds between full user polls (default: 60)             |
| verbose          | Enable detailed logging                                  |

## Usage

```bash
# Using a config file
python3 -m mxgreeter --config config.toml

# Using CLI arguments
mxgreeter -s http://localhost:8008 -u greeter -p greeter --server-name example -r General
```

On first run, the bot auto-registers with Synapse and saves credentials to `~/.config/mxgreeter/credentials/`. Subsequent runs reuse the saved session.

## License

MIT
