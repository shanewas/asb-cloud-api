import argparse
import asyncio
import json
import os
import sys

from asb_api.db import db, run_migrations
from asb_api.db.auth_store import PostgresKeyStore


async def _get_store() -> PostgresKeyStore:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL env var required for admin commands (Phase 2+).", file=sys.stderr)
        sys.exit(2)
    db.dsn = dsn
    await db.connect()
    await run_migrations()
    return PostgresKeyStore()


async def _close():
    try:
        await db.disconnect()
    except Exception:
        pass


async def async_main():
    parser = argparse.ArgumentParser(description="ASB Cloud API Admin CLI (Phase 2 - PostgreSQL)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-keys", help="List all API keys")

    create_parser = sub.add_parser("create-key", help="Create a new API key")
    create_parser.add_argument("--tier", default="free", choices=["free", "starter", "pro", "enterprise"])
    create_parser.add_argument("--email", default=None)

    revoke_parser = sub.add_parser("revoke-key", help="Revoke an API key")
    revoke_parser.add_argument("key_id", help="Key ID to revoke")

    args = parser.parse_args()
    store = await _get_store()

    try:
        if args.command == "list-keys":
            keys = await store.list_keys()
            print(json.dumps(keys, indent=2))
        elif args.command == "create-key":
            raw, key = await store.create(tier=args.tier, owner_email=args.email)
            print(f"Key ID: {key['key_id']}")
            print(f"Tier: {key['tier']}")
            print(f"Raw key (save this, shown only once): {raw}")
        elif args.command == "revoke-key":
            await store.revoke(args.key_id)
            print(f"Key {args.key_id} revoked.")
    finally:
        await _close()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
