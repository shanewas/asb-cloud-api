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

    async def run(self, url, method, headers, data, proxy, fingerprint, timeout, screenshot):
        proxy_config = None
        if proxy and proxy.host != "DIRECT":
            proxy_config = {
                "server": f"{proxy.protocol}://{proxy.host}:{proxy.port}",
                "username": proxy.username,
                "password": proxy.password,
            }

        context = await self.browser.new_context(
            user_agent=fingerprint.user_agent,
            viewport={"width": fingerprint.viewport[0], "height": fingerprint.viewport[1]},
            proxy=proxy_config,
            extra_http_headers=headers or {},
        )
        page = await context.new_page()

        try:
            if method == "POST":
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            else:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            html = await page.content()
            cookies = await context.cookies()
            headers_out = dict(response.headers) if response else {}

            screenshot_url = None
            if screenshot:
                screenshot_url = f"/tmp/screenshots/{uuid.uuid4().hex}.png"
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
