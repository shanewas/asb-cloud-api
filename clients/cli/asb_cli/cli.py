"""ASB CLI - thin command line interface for smoke tests and local workflows."""

import argparse
import json
import os
import sys

from asb_client import AsbClient, AsbError


def get_client(args: argparse.Namespace) -> AsbClient:
    base_url = args.base_url or os.getenv("ASB_BASE_URL", "http://localhost:8000")
    api_key = args.api_key or os.getenv("ASB_API_KEY")
    return AsbClient(base_url=base_url, api_key=api_key, timeout=120)


def pretty(obj: dict | list) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="asb",
        description="Thin CLI for ASB Cloud API (scrape, sessions, usage, billing).",
    )
    parser.add_argument("--base-url", help="API base URL (default: http://localhost:8000 or ASB_BASE_URL)")
    parser.add_argument("--api-key", help="API key (default: ASB_API_KEY env)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--version", action="version", version="asb-cli 0.1.0")

    sub = parser.add_subparsers(dest="cmd", required=True)

    # health
    p = sub.add_parser("health", help="Check service health (no auth)")
    p.set_defaults(func=cmd_health)

    # scrape
    p = sub.add_parser("scrape", help="Execute a browser scrape")
    p.add_argument("--url", required=True)
    p.add_argument("--method", choices=["GET", "POST"], default="GET")
    p.add_argument("--data", help="JSON string for POST body")
    p.add_argument("--headers", help="JSON string for extra headers")
    p.add_argument("--region", default="jp")
    p.add_argument("--fingerprint")
    p.add_argument("--proxy-provider")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--screenshot", action="store_true")
    p.add_argument("--session-id")
    p.add_argument("--session-type", choices=["stateless", "stateful", "stateful_reset"], default="stateless")
    p.set_defaults(func=cmd_scrape)

    # sessions group
    ps = sub.add_parser("session", help="Session management")
    ssub = ps.add_subparsers(dest="subcmd", required=True)

    p = ssub.add_parser("create", help="Create stateful session")
    p.add_argument("--region", default="jp")
    p.add_argument("--fingerprint")
    p.set_defaults(func=cmd_session_create)

    p = ssub.add_parser("get", help="Inspect session")
    p.add_argument("session_id")
    p.set_defaults(func=cmd_session_get)

    p = ssub.add_parser("delete", help="Delete session")
    p.add_argument("session_id")
    p.set_defaults(func=cmd_session_delete)

    # usage
    p = sub.add_parser("usage", help="Show current usage for the key")
    p.set_defaults(func=cmd_usage)

    # billing
    p = sub.add_parser("billing", help="Billing helpers")
    bsub = p.add_subparsers(dest="subcmd", required=True)
    bp = bsub.add_parser("portal", help="Get Stripe customer portal link")
    bp.set_defaults(func=cmd_billing_portal)

    args = parser.parse_args(argv)
    try:
        client = get_client(args)
        result = args.func(client, args)
        if result is not None:
            if args.pretty or os.getenv("ASB_PRETTY"):
                print(pretty(result))
            else:
                print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
        return 0
    except AsbError as e:
        print(f"Error: {e}", file=sys.stderr)
        if hasattr(e, "status_code") and e.status_code:
            print(f"Status: {e.status_code}", file=sys.stderr)
        if e.response:
            print(json.dumps(e.response, indent=2), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 2


def cmd_health(client: AsbClient, args: argparse.Namespace):
    return client.health()


def cmd_scrape(client: AsbClient, args: argparse.Namespace):
    headers = json.loads(args.headers) if args.headers else None
    data = json.loads(args.data) if args.data else None
    return client.scrape(
        url=args.url,
        method=args.method,
        headers=headers,
        data=data,
        proxy_provider=args.proxy_provider,
        region=args.region,
        fingerprint=args.fingerprint,
        timeout=args.timeout,
        screenshot=args.screenshot,
        session_id=args.session_id,
        session_type=args.session_type,
    )


def cmd_session_create(client: AsbClient, args: argparse.Namespace):
    return client.create_session(region=args.region, fingerprint=args.fingerprint)


def cmd_session_get(client: AsbClient, args: argparse.Namespace):
    return client.get_session(args.session_id)


def cmd_session_delete(client: AsbClient, args: argparse.Namespace):
    client.delete_session(args.session_id)
    print("Deleted", file=sys.stderr)
    return None


def cmd_usage(client: AsbClient, args: argparse.Namespace):
    return client.get_usage()


def cmd_billing_portal(client: AsbClient, args: argparse.Namespace):
    return client.get_billing_portal()


if __name__ == "__main__":
    sys.exit(main())
