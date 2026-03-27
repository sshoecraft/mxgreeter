"""mxgreeter CLI -- run the auto-join bot.

Usage:
    python3 -m mxgreeter --server URL --username USER --password PASS [options]
    python3 -m mxgreeter --config /path/to/config.toml

v0.2.0
"""

import argparse
import asyncio
import signal
import sys
import tomllib

from . import VERSION
from .bot import Greeter


def load_config(path: str) -> dict:
    """Load config from a TOML file."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    parser = argparse.ArgumentParser(
        prog="mxgreeter",
        description="Auto-join bot for Matrix/Synapse",
    )
    parser.add_argument("--config", "-c", default=None,
                        help="Path to TOML config file")
    parser.add_argument("--server", "-s", default=None,
                        help="Matrix homeserver URL (e.g. http://localhost:8008)")
    parser.add_argument("--username", "-u", default=None,
                        help="Bot username (must be a Synapse admin)")
    parser.add_argument("--password", "-p", default=None,
                        help="Bot password")
    parser.add_argument("--server-name", default=None,
                        help="Matrix server name (e.g. mckesson)")
    parser.add_argument("--room", "-r", action="append", default=None,
                        help="Room to auto-join users into (repeatable)")
    parser.add_argument("--poll-interval", type=int, default=None,
                        help="Seconds between full user polls (default: 60)")
    parser.add_argument("--verbose", "-v", action="store_true", default=False,
                        help="Verbose output")

    args = parser.parse_args()

    # Load from config file if provided
    config = {}
    if args.config:
        try:
            config = load_config(args.config)
        except FileNotFoundError:
            print(f"Config file not found: {args.config}")
            sys.exit(1)

    # CLI overrides
    if args.server:
        config["server"] = args.server
    if args.username:
        config["username"] = args.username
    if args.password:
        config["password"] = args.password
    if args.server_name:
        config["server_name"] = args.server_name
    if args.room:
        config["rooms"] = args.room
    if args.poll_interval is not None:
        config["poll_interval"] = args.poll_interval
    if args.verbose:
        config["verbose"] = True

    # Validate
    required = ["server", "username", "password", "server_name", "rooms"]
    missing = [f for f in required if not config.get(f)]
    if missing:
        print(f"Missing required config: {', '.join(missing)}")
        print("Provide via --config file or CLI args")
        sys.exit(1)

    bot = Greeter(
        homeserver=config["server"],
        username=config["username"],
        password=config["password"],
        server_name=config["server_name"],
        rooms=config["rooms"],
        admin_user=config.get("admin_user"),
        admin_password=config.get("admin_password"),
        poll_interval=config.get("poll_interval", 60),
        verbose=config.get("verbose", False),
    )

    print(f"mxgreeter v{VERSION}", flush=True)
    print(f"  server: {config['server']}", flush=True)
    print(f"  bot user: @{config['username']}:{config['server_name']}", flush=True)
    print(f"  rooms: {config['rooms']}", flush=True)

    async def run():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        bot_task = asyncio.create_task(bot.start())
        stop_task = asyncio.create_task(stop_event.wait())

        done, pending = await asyncio.wait(
            [bot_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()

        print("\n  [mxgreeter] shutting down...", flush=True)
        await bot.stop()

    try:
        asyncio.run(run())
    except Exception as e:
        print(f"Error: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
