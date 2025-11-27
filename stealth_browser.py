import logging
import os
import random
import time
from contextlib import contextmanager
from typing import Generator, Optional

from playwright.sync_api import Browser, TimeoutError, sync_playwright
# CRITICAL IMPORT: We correctly import the Stealth class.
from playwright_stealth import Stealth 

logger = logging.getLogger(__name__)

WINDOWS_USER_AGENT = os.environ.get(
    "PLAYWRIGHT_USER_AGENT",
    # Using a common Chrome user agent is often more effective with Playwright/Chromium
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
ROTATING_USER_AGENTS = [
    WINDOWS_USER_AGENT,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
]
_user_agent_calls = 0
# Use chromium as the default since stealth is often best on Chromium
DEFAULT_BROWSER = os.environ.get("PLAYWRIGHT_BROWSER", "chromium") 


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


# --- REFACTORING LAUNCH_STEALTH_BROWSER TO APPLY STEALTH CORRECTLY ---
@contextmanager
def launch_stealth_browser(
    browser_type: str = DEFAULT_BROWSER, 
    headless: bool = False # CRITICAL: Changed default to False for better stealth
) -> Generator[Browser, None, None]:
    """Launch a Playwright browser with stealth enabled and a Windows user agent."""
    # CRITICAL: Stealth must wrap sync_playwright() here.
    with Stealth().use_sync(sync_playwright()) as p: 
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")
        
        # Use args for stealth/anti-detection
        browser = launcher.launch(
            headless=headless, 
            args=["--disable-blink-features=AutomationControlled"]
        )
        try:
            yield browser
        finally:
            browser.close()


def fetch_with_stealth(url: str, timeout: int = 60, browser_type: str = DEFAULT_BROWSER) -> str:
    logger.debug("Stealth fetching url=%s with browser=%s", url, browser_type)
    # Use the refactored launch_stealth_browser which now correctly applies Stealth
    with launch_stealth_browser(browser_type, headless=False) as browser: # Explicitly setting headless=False
        context = browser.new_context(user_agent=WINDOWS_USER_AGENT)
        page = context.new_page()
        # Removed the ineffective Stealth().use_sync(sync_playwright(page))
        
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
    """Inspect the page for Cloudflare IUAM/Turnstile/DDoS-Guard markers."""
    try:
        title = page.title()
    except TimeoutError:
        title = "TITLE_FETCH_TIMEOUT"

    is_challenge_text = False
    
    # Check for common challenge texts (Cloudflare and general checks)
    challenge_locators = [
        "text=Checking your browser before accessing", # Cloudflare
        "text=DDOS-GUARD", # DDoS-Guard (often remains in title)
        'iframe[src*="captcha"]', # Captcha
        'iframe[src*="turnstile"]' # Captcha
    ]

    for selector in challenge_locators:
        try:
            # We don't care if it's visible, just if the element is present in the DOM
            if page.locator(selector).count() > 0: 
                is_challenge_text = True
                break
        except Exception:
            pass # Ignore any other exceptions
            
    # Check current URL, as DDoS-Guard often redirects to a final destination.
    current_url = page.url

    logger.debug(
        "Cloudflare status title=%r URL=%r Challenge Indicator=%s",
        title,
        current_url,
        is_challenge_text,
    )

    if "Access Denied" in title or "Forbidden" in title:
        return "BLOCKED"
    
    # If a specific challenge text is found, or the title is DDOS-GUARD, we are still waiting
    if is_challenge_text or "DDOS-GUARD" in title.upper() or "CHECKING YOUR BROWSER" in title.upper():
        return "CHALLENGED"
    
    # If the title has changed from a challenge page to a normal page title, it's a success.
    # We assume any page not matching the challenge indicators is the target content.
    return "SUCCESS"


def solve_cloudflare_challenge(
    url: str,
    timeout: int = 90, # Increased timeout to 90s
    wait_seconds: int = 90, # Increased challenge wait time to match
    browser_type: str = DEFAULT_BROWSER,
) -> Optional[str]:
    """
    Run a stealth Playwright session to wait out Cloudflare/DDoS-Guard pages.
    """

    user_agent = _next_user_agent()
    logger.info(
        "Attempting Cloudflare bypass for %s with user agent %s", url, user_agent
    )
    
    # CRITICAL FIX: Ensure Stealth is applied before launching the browser
    with Stealth().use_sync(sync_playwright()) as p: 
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")

        # Launch browser with anti-detection arguments and HEADLESS=FALSE
        browser = launcher.launch(
            headless=False, # <-- The major change to bypass DDoS-Guard detection
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # New context with rotating user agent
        context = browser.new_context(
            user_agent=user_agent, viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        try:
            # Load the page and wait for initial network activity to settle
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000) # Changed to domcontentloaded for faster initial check
            
            start_time = time.time()
            status = "CHALLENGED"

            # Use a shorter, less aggressive sleep for better responsiveness
            while (time.time() - start_time) < wait_seconds and status == "CHALLENGED":
                status = _check_cloudflare_status(page)
                if status in {"SUCCESS", "BLOCKED"}:
                    break
                # Wait only 3 seconds
                time.sleep(3) 

            if status == "SUCCESS":
                logger.info("Challenge solved for %s", url)
                # Give the page a moment to fully render after the redirect/challenge completion
                page.wait_for_load_state("networkidle", timeout=10000)
                return page.content()

            if status == "BLOCKED":
                logger.warning("Access permanently blocked to %s", url)
                return None

            logger.warning("Timed out waiting for challenge to resolve for %s", url)
            return None
        
        except TimeoutError:
            logger.error("Navigation or wait timed out for %s", url)
            return None
            
        finally:
            browser.close()
