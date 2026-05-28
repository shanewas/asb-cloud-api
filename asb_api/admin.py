import argparse
import json
from asb_api.api.auth import InMemoryKeyStore


def main():
    parser = argparse.ArgumentParser(description="ASB Cloud API Admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-keys", help="List all API keys")

    create_parser = sub.add_parser("create-key", help="Create a new API key")
    create_parser.add_argument("--tier", default="free", choices=["free", "starter", "pro", "enterprise"])
    create_parser.add_argument("--email", default=None)

    revoke_parser = sub.add_parser("revoke-key", help="Revoke an API key")
    revoke_parser.add_argument("key_id", help="Key ID to revoke")

    args = parser.parse_args()
    store = InMemoryKeyStore()

    if args.command == "list-keys":
        keys = store.list_keys()
        print(json.dumps(keys, indent=2))
    elif args.command == "create-key":
        raw, key = store.create(tier=args.tier, owner_email=args.email)
        print(f"Key ID: {key.key_id}")
        print(f"Tier: {key.tier}")
        print(f"Raw key (save this, shown only once): {raw}")
    elif args.command == "revoke-key":
        store.revoke(args.key_id)
        print(f"Key {args.key_id} revoked.")


if __name__ == "__main__":
    main()
