# Python Client Examples

These examples assume you have a running local ASB API (self-hosted / in-memory mode is fine).

## Setup

```bash
# Terminal 1: start API (copy the sk_live_... key it prints)
cd /path/to/asb-cloud-api
python -m asb_api

# Terminal 2
export ASB_API_KEY=sk_live_...   # from API logs
pip install -e clients/python
python examples/python/scrape_basic.py
```

Run all:
```bash
python -m pytest examples/python/ -q --tb=line || python examples/python/*.py
```
