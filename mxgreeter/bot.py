"""Greeter bot -- monitors presence and force-joins users to required rooms.

Two mechanisms ensure coverage:
1. Presence events (event-driven) -- fires when a user comes online
2. Periodic polling (safety net) -- catches anyone the presence events missed

Uses the Synapse Admin API for force-joins so users don't need to accept invites.
Auto-registers on first run (same flow as mxai).

v0.2.0
"""

import asyncio
import json
import os
import time
from urllib.parse import quote

import aiohttp
from nio import (
    AsyncClient,
    LoginResponse,
    PresenceEvent,
    RoomResolveAliasResponse,
)

from . import VERSION


CREDENTIALS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "mxgreeter", "credentials",
)


def save_credentials(username: str, user_id: str, access_token: str,
                     device_id: str, homeserver: str):
    """Save credentials to disk for reuse across restarts."""
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)
    path = os.path.join(CREDENTIALS_DIR, f"{username}.json")
    data = {
        "user_id": user_id,
        "access_token": access_token,
        "device_id": device_id,
        "homeserver": homeserver,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def load_credentials(username: str) -> dict | None:
    """Load saved credentials from disk. Returns None if not found."""
    path = os.path.join(CREDENTIALS_DIR, f"{username}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


async def register(homeserver: str, username: str, password: str) -> dict:
    """Register a new Matrix account via the Synapse dummy auth flow.

    Returns the registration response dict (user_id, access_token, device_id).
    Raises RuntimeError on failure.
    """
    url = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
    payload = {
        "username": username,
        "password": password,
        "kind": "user",
    }

    async with aiohttp.ClientSession() as session:
        # Step 1: initial request to get session
        async with session.post(url, json=payload) as resp:
            data = await resp.json()

            if resp.status == 200:
                return data

            if resp.status != 401:
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Registration failed: {error}")

            # Step 2: complete with dummy auth
            session_id = data.get("session")
            if not session_id:
                raise RuntimeError("Registration failed: no session in 401 response")

            payload["auth"] = {
                "type": "m.login.dummy",
                "session": session_id,
            }

            async with session.post(url, json=payload) as resp2:
                data2 = await resp2.json()

                if resp2.status == 200:
                    return data2

                error = data2.get("error", "Unknown error")
                raise RuntimeError(f"Registration failed: {error}")


async def promote_to_admin(homeserver: str, user_id: str, token: str):
    """Promote a user to Synapse server admin via the Admin API."""
    url = f"{homeserver.rstrip('/')}/_synapse/admin/v2/users/{user_id}"
    headers = {"Authorization": f"Bearer {token}"}
    body = {"admin": True}

    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=body) as resp:
            if resp.status == 200:
                print(f"  [mxgreeter] promoted {user_id} to admin", flush=True)
            else:
                text = await resp.text()
                print(f"  [mxgreeter] admin promotion failed: {resp.status} {text}", flush=True)
                print(f"  [mxgreeter] the bot user must be a Synapse admin for force-joins to work",
                      flush=True)
                print(f"  [mxgreeter] run: docker exec synapse-synapse-1 register_new_matrix_user "
                      f"-c /data/homeserver.yaml -a -u {user_id.split(':')[0][1:]} -p <password> "
                      f"http://localhost:8008", flush=True)


class Greeter:
    """Auto-join bot that ensures users are in the configured rooms."""

    def __init__(self, homeserver: str, username: str, password: str,
                 server_name: str, rooms: list, admin_user: str = None,
                 admin_password: str = None, poll_interval: int = 60,
                 verbose: bool = False):
        self.homeserver = homeserver.rstrip("/")
        self.username = username
        self.password = password
        self.server_name = server_name
        self.rooms = rooms
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.poll_interval = poll_interval
        self.verbose = verbose

        self.client = None
        self.admin_token = None
        self.room_ids = {}          # alias -> room_id cache
        self.recently_checked = {}  # user_id -> timestamp of last check
        self.check_cooldown = 300   # don't re-check a user within 5 minutes

    async def start(self):
        """Authenticate, join rooms, and begin monitoring."""
        await self._authenticate()

        # Join all required rooms ourselves
        for room_alias in self.rooms:
            alias = self._normalize_alias(room_alias)
            resp = await self.client.join(alias)
            if hasattr(resp, "room_id"):
                self.room_ids[alias] = resp.room_id
                print(f"  [mxgreeter] joined {alias} ({resp.room_id})", flush=True)
            else:
                print(f"  [mxgreeter] failed to join {alias}: {resp}", flush=True)

        # Register presence callback
        self.client.add_presence_callback(self._on_presence, PresenceEvent)

        print(f"  [mxgreeter] monitoring {len(self.rooms)} room(s)", flush=True)
        print(f"  [mxgreeter] poll interval: {self.poll_interval}s", flush=True)
        print(f"  [mxgreeter] waiting for users...", flush=True)

        # Run sync + periodic poll concurrently
        sync_task = asyncio.create_task(
            self.client.sync_forever(timeout=30000)
        )
        poll_task = asyncio.create_task(self._periodic_poll())

        done, pending = await asyncio.wait(
            [sync_task, poll_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()

    async def stop(self):
        """Shut down cleanly."""
        if self.client:
            await self.client.close()
        print("  [mxgreeter] stopped", flush=True)

    # -- authentication --

    async def _authenticate(self):
        """Register (first run) or login using saved credentials, like mxai."""
        saved = load_credentials(self.username)

        if saved:
            print(f"  [mxgreeter] using saved credentials", flush=True)
            self.client = AsyncClient(saved["homeserver"], saved["user_id"])
            self.client.access_token = saved["access_token"]
            self.client.device_id = saved["device_id"]
            self.client.user_id = saved["user_id"]
            self.admin_token = saved["access_token"]
            print(f"  [mxgreeter] logged in as {self.client.user_id}", flush=True)

            if self.admin_user and self.admin_password:
                await self._get_admin_token()
            return

        # Try to register first
        print(f"  [mxgreeter] no saved credentials, registering @{self.username}...", flush=True)
        try:
            reg_data = await register(self.homeserver, self.username, self.password)
            print(f"  [mxgreeter] registered successfully", flush=True)

            self.client = AsyncClient(self.homeserver, reg_data["user_id"])
            self.client.access_token = reg_data["access_token"]
            self.client.device_id = reg_data["device_id"]
            self.client.user_id = reg_data["user_id"]
            self.admin_token = reg_data["access_token"]

            save_credentials(
                self.username,
                reg_data["user_id"],
                reg_data["access_token"],
                reg_data["device_id"],
                self.homeserver,
            )
            print(f"  [mxgreeter] credentials saved", flush=True)
            print(f"  [mxgreeter] logged in as {self.client.user_id}", flush=True)

        except RuntimeError as e:
            if "User ID already taken" in str(e):
                print(f"  [mxgreeter] already registered, logging in...", flush=True)
            else:
                raise

            # Fall back to login
            user_id = f"@{self.username}:{self.server_name}"
            self.client = AsyncClient(self.homeserver, user_id)
            resp = await self.client.login(self.password)
            if not isinstance(resp, LoginResponse):
                raise RuntimeError(f"Login failed: {resp}")

            self.admin_token = self.client.access_token

            save_credentials(
                self.username,
                resp.user_id,
                resp.access_token,
                resp.device_id,
                self.homeserver,
            )
            print(f"  [mxgreeter] credentials saved", flush=True)
            print(f"  [mxgreeter] logged in as {self.client.user_id}", flush=True)

        # If a separate admin user is configured, get an admin token from them
        if self.admin_user and self.admin_password:
            await self._get_admin_token()

    async def _get_admin_token(self):
        """Login as the admin user to get a token for Admin API calls."""
        admin_id = f"@{self.admin_user}:{self.server_name}"
        admin_client = AsyncClient(self.homeserver, admin_id)
        resp = await admin_client.login(self.admin_password)
        if isinstance(resp, LoginResponse):
            self.admin_token = admin_client.access_token
            print(f"  [mxgreeter] admin token acquired from @{self.admin_user}", flush=True)
        else:
            print(f"  [mxgreeter] admin login failed: {resp}", flush=True)
            print(f"  [mxgreeter] falling back to bot token (force-joins may fail if not admin)",
                  flush=True)
        await admin_client.close()

    # -- presence handler --

    async def _on_presence(self, event: PresenceEvent):
        """User came online -- check their room membership."""
        if event.user_id == self.client.user_id:
            return

        if event.presence != "online":
            return

        # Cooldown: don't hammer the admin API for the same user
        now = time.time()
        last = self.recently_checked.get(event.user_id, 0)
        if now - last < self.check_cooldown:
            return

        self.recently_checked[event.user_id] = now

        if self.verbose:
            print(f"  [mxgreeter] presence: {event.user_id} -> online", flush=True)

        await self._ensure_user_in_rooms(event.user_id)

    # -- periodic polling --

    async def _periodic_poll(self):
        """Periodically check all server users via admin API."""
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                users = await self._admin_list_users()
                count = 0
                for user in users:
                    user_id = user["name"]
                    if user_id == self.client.user_id:
                        continue
                    if user.get("deactivated"):
                        continue
                    await self._ensure_user_in_rooms(user_id)
                    count += 1

                if self.verbose:
                    print(f"  [mxgreeter] poll: checked {count} users", flush=True)

            except Exception as e:
                print(f"  [mxgreeter] poll error: {e}", flush=True)

    # -- room membership enforcement --

    async def _ensure_user_in_rooms(self, user_id: str):
        """Check if a user is in all required rooms; force-join if not."""
        try:
            joined = await self._admin_get_user_rooms(user_id)
        except Exception as e:
            if self.verbose:
                print(f"  [mxgreeter] can't get rooms for {user_id}: {e}", flush=True)
            return

        joined_set = set(joined)

        for room_alias in self.rooms:
            alias = self._normalize_alias(room_alias)
            room_id = await self._resolve_room(alias)
            if not room_id:
                continue

            if room_id not in joined_set:
                await self._admin_force_join(room_id, user_id)

    async def _resolve_room(self, alias: str) -> str:
        """Resolve a room alias to a room ID (cached)."""
        if alias in self.room_ids:
            return self.room_ids[alias]

        resp = await self.client.room_resolve_alias(alias)
        if isinstance(resp, RoomResolveAliasResponse):
            self.room_ids[alias] = resp.room_id
            return resp.room_id

        print(f"  [mxgreeter] can't resolve {alias}: {resp}", flush=True)
        return None

    # -- Synapse Admin API --

    async def _admin_list_users(self) -> list:
        """List all local users via Synapse Admin API."""
        url = f"{self.homeserver}/_synapse/admin/v2/users?from=0&limit=10000&guests=false"
        headers = {"Authorization": f"Bearer {self.admin_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"list users: {resp.status} {text}")
                data = await resp.json()
                return data.get("users", [])

    async def _admin_get_user_rooms(self, user_id: str) -> list:
        """Get the rooms a user has joined via Admin API."""
        url = f"{self.homeserver}/_synapse/admin/v1/users/{user_id}/joined_rooms"
        headers = {"Authorization": f"Bearer {self.admin_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"get rooms: {resp.status} {text}")
                data = await resp.json()
                return data.get("joined_rooms", [])

    async def _admin_force_join(self, room: str, user_id: str):
        """Force-join a user into a room via Synapse Admin API."""
        url = f"{self.homeserver}/_synapse/admin/v1/join/{quote(room, safe='')}"
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        body = {"user_id": user_id}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status == 200:
                    print(f"  [mxgreeter] joined {user_id} -> {room}", flush=True)
                else:
                    text = await resp.text()
                    print(f"  [mxgreeter] force-join failed {user_id} -> {room}: {resp.status} {text}",
                          flush=True)

    # -- helpers --

    def _normalize_alias(self, room: str) -> str:
        """Ensure a room string is a full alias like #General:mckesson."""
        if room.startswith("#") or room.startswith("!"):
            return room
        return f"#{room}:{self.server_name}"
