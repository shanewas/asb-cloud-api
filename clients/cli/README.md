# asb-cli

Small official CLI for the ASB Cloud API. Ideal for smoke tests, local development, and quick workflows.

## Installation

```bash
pip install asb-cli
```

It pulls in `asb-cloud-client` automatically.

From source (monorepo):

```bash
pip install -e clients/python -e clients/cli
```

## Configuration

- `ASB_API_KEY` env var (required for authenticated commands)
- `ASB_BASE_URL` env var (defaults to http://localhost:8000 for self-hosted)
- Flags `--api-key` / `--base-url` override

## Commands

```bash
# Health (public)
asb health

# Scrape a page
asb scrape --url https://example.com --method GET --region jp --timeout 30

# POST example
asb scrape --url https://httpbin.org/post --method POST --data '{"hello":"world"}'

# Sessions
asb session create --region jp --fingerprint general
asb session get <session_id>
asb session delete <session_id>

# Usage & billing
asb usage
asb billing portal
```

Output is compact JSON by default (pipeable). Use `--pretty` for human readable where helpful.

## Local Self-Hosted

Start the API in another terminal:

```bash
python -m asb_api
# copy the sk_live_... key from logs
export ASB_API_KEY=sk_live_...
asb health
asb scrape --url https://example.com
```

See `../../examples/cli/` for more.
