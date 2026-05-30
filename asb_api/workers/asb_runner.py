import json
import os
import uuid
from playwright.async_api import async_playwright


class ASBRunner:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
        )
        return self

    async def __aexit__(self, *args):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def close(self):
        await self.__aexit__()

    @staticmethod
    def _prepare_body(headers, data):
        request_headers = dict(headers or {})
        if data is None:
            return request_headers, None
        if isinstance(data, (str, bytes)):
            return request_headers, data

        has_content_type = any(k.lower() == "content-type" for k in request_headers)
        if not has_content_type:
            request_headers["Content-Type"] = "application/json"
        return request_headers, json.dumps(data)

    async def run(self, url, method, headers, data, proxy, fingerprint, timeout, screenshot, screenshot_dir: str | None = None):
        proxy_config = None
        if proxy and proxy.host != "DIRECT":
            proxy_config = {
                "server": f"{proxy.protocol}://{proxy.host}:{proxy.port}",
                "username": proxy.username,
                "password": proxy.password,
            }

        request_headers, request_body = self._prepare_body(headers, data)
        context = await self.browser.new_context(
            user_agent=fingerprint.user_agent,
            viewport={"width": fingerprint.viewport[0], "height": fingerprint.viewport[1]},
            proxy=proxy_config,
            extra_http_headers=request_headers,
        )
        page = await context.new_page()

        try:
            if method.upper() == "POST":
                response = await context.request.fetch(
                    url,
                    method="POST",
                    headers=request_headers,
                    data=request_body,
                    timeout=timeout * 1000,
                )
                html = await response.text()
                await page.set_content(html, wait_until="domcontentloaded", timeout=timeout * 1000)
            else:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                html = await page.content()

            cookies = await context.cookies()
            headers_out = dict(response.headers) if response else {}

            screenshot_url = None
            if screenshot and screenshot_dir:
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_url = os.path.join(screenshot_dir, f"{uuid.uuid4().hex}.png")
                await page.screenshot(path=screenshot_url)

            return {
                "html": html,
                "cookies": {c["name"]: c["value"] for c in cookies},
                "headers": headers_out,
                "screenshot_url": screenshot_url,
                "block_detected": False,
            }
        finally:
            await context.close()
