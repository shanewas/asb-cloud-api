#!/usr/bin/env python3
"""Basic smoke test using the Python client against local self-hosted API."""

import os
import sys

# Allow running from repo root without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../clients/python"))

from asb_client import AsbClient, AsbError


def main():
    base = os.getenv("ASB_BASE_URL", "http://localhost:8000")
    key = os.getenv("ASB_API_KEY")
    if not key:
        print("ERROR: Set ASB_API_KEY (from `python -m asb_api` startup logs)", file=sys.stderr)
        sys.exit(1)

    client = AsbClient(base_url=base, api_key=key)

    print("== Health ==")
    h = client.health()
    print(h)

    print("\n== Scrape (GET example.com) ==")
    try:
        r = client.scrape(url="https://example.com", method="GET", region="jp", timeout=20)
        print("status:", r.get("status"))
        print("html_len:", len(r.get("html") or ""))
        print("request_id:", r.get("metadata", {}).get("request_id") if r.get("metadata") else None)
    except AsbError as e:
        print("Scrape failed:", e)
        sys.exit(2)

    print("\n== Create session ==")
    try:
        s = client.create_session(region="jp", fingerprint="general")
        print(s)
        sid = s["session_id"]

        print("\n== Get session ==")
        print(client.get_session(sid))

        print("\n== Delete session ==")
        client.delete_session(sid)
        print("deleted ok")
    except AsbError as e:
        print("Session ops failed (may be ok if no persistence):", e)

    print("\n== Usage ==")
    try:
        u = client.get_usage()
        print(u)
    except AsbError as e:
        print("Usage failed:", e)

    print("\n== Billing portal (optional) ==")
    try:
        p = client.get_billing_portal()
        print(p)
    except AsbError as e:
        print("Billing portal not available (expected in self-hosted without Stripe):", e)

    print("\nAll basic examples completed successfully.")


if __name__ == "__main__":
    main()
