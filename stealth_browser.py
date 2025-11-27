import logging
import os
import random
import time
from contextlib import contextmanager
from typing import Generator, Optional

from playwright.sync_api import Browser, TimeoutError, sync_playwright
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

WINDOWS_USER_AGENT = os.environ.get(
    "PLAYWRIGHT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
)
ROTATING_USER_AGENTS = [
    WINDOWS_USER_AGENT,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
_user_agent_calls = 0
DEFAULT_BROWSER = os.environ.get("PLAYWRIGHT_BROWSER", "firefox")


def is_cloudflare_challenge(text: str, status_code: int, headers: dict) -> bool:
    """Heuristically detect Cloudflare/anti-bot interstitials."""
    server_header = (headers or {}).get("Server", "").lower()
    cf_ray = (headers or {}).get("cf-ray") or (headers or {}).get("CF-RAY")
    indicators = [
        "attention required",
        "checking your browser before accessing",
        "just a moment",
        "cloudflare",
        "verify you are human",
    ]
    text_lower = text.lower()
    return (
        status_code in {403, 503}
        or "cloudflare" in server_header
        or cf_ray is not None
        or any(indicator in text_lower for indicator in indicators)
    )


@contextmanager
def launch_stealth_browser(browser_type: str = DEFAULT_BROWSER) -> Generator[Browser, None, None]:
    """Launch a Playwright browser with stealth enabled and a Windows user agent."""
    with sync_playwright() as p:
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")
        browser = launcher.launch(headless=False)
        try:
            yield browser
        finally:
            browser.close()


def fetch_with_stealth(url: str, timeout: int = 30, browser_type: str = DEFAULT_BROWSER) -> str:
    logger.debug("Stealth fetching url=%s with browser=%s", url, browser_type)
    with launch_stealth_browser(browser_type) as browser:
        context = browser.new_context(user_agent=WINDOWS_USER_AGENT)
        page = context.new_page()
        Stealth().use_sync(sync_playwright(page))
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        page.wait_for_timeout(300)
        html = page.content()
        context.close()
        logger.debug("Fetched %d characters from %s", len(html), url)
        return html


def _next_user_agent() -> str:
    """Rotate user agents occasionally to reduce fingerprint reuse."""
    global _user_agent_calls
    _user_agent_calls += 1
    if _user_agent_calls % 3 == 0:
        return random.choice(ROTATING_USER_AGENTS)
    return WINDOWS_USER_AGENT


def _check_cloudflare_status(page) -> str:
    """Inspect the page for Cloudflare IUAM/Turnstile markers."""
    try:
        title = page.title()
    except TimeoutError:
        title = "TITLE_FETCH_TIMEOUT"

    try:
        is_challenge_text = page.locator(
            "text=Checking your browser before accessing"
        ).is_visible(timeout=1000)
    except Exception:
        is_challenge_text = False

    try:
        is_captcha = page.locator(
            'iframe[src*="captcha"], iframe[src*="turnstile"]'
        ).is_visible(timeout=1000)
    except Exception:
        is_captcha = False

    logger.debug(
        "Cloudflare status title=%r challenge_text=%s captcha=%s",
        title,
        is_challenge_text,
        is_captcha,
    )

    if "Access Denied" in title or "Forbidden" in title:
        return "BLOCKED"
    if is_challenge_text or is_captcha:
        return "CHALLENGED"
    if "Checking your browser" not in title and "Cloudflare" not in title:
        return "SUCCESS"
    return "CHALLENGED"


def solve_cloudflare_challenge(
    url: str,
    timeout: int = 60,
    wait_seconds: int = 30,
    browser_type: str = DEFAULT_BROWSER,
) -> Optional[str]:
    """Run a stealth Playwright session to wait out Cloudflare DDOS pages."""

    user_agent = _next_user_agent()
    logger.info(
        "Attempting Cloudflare bypass for %s with user agent %s", url, user_agent
    )

    with Stealth().use_sync(sync_playwright()) as p:
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")

        browser = launcher.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent=user_agent, viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="load", timeout=timeout * 1000)
            start_time = time.time()
            status = "CHALLENGED"

            while (time.time() - start_time) < wait_seconds and status == "CHALLENGED":
                status = _check_cloudflare_status(page)
                if status in {"SUCCESS", "BLOCKED"}:
                    break
                time.sleep(2)

            if status == "SUCCESS":
                logger.info("Cloudflare challenge solved for %s", url)
                return page.content()

            if status == "BLOCKED":
                logger.warning("Cloudflare permanently blocked access to %s", url)
                return None

            logger.warning("Timed out waiting for Cloudflare to resolve for %s", url)
            return None
        finally:
            browser.close()
