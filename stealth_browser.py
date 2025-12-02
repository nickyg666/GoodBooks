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


@contextmanager
def launch_stealth_browser(
    browser_type: str = DEFAULT_BROWSER, 
    headless: bool = False 
) -> Generator[Browser, None, None]:
    """Launch a Playwright browser with stealth enabled and a Windows user agent."""
    with Stealth().use_sync(sync_playwright()) as p: 
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")
        
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
    with launch_stealth_browser(browser_type, headless=False) as browser: 
        context = browser.new_context(user_agent=WINDOWS_USER_AGENT)
        page = context.new_page()
        
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
        "text=Checking your browser before accessing", 
        "text=DDOS-GUARD", 
        'iframe[src*="captcha"]', 
        'iframe[src*="turnstile"]' 
    ]

    for selector in challenge_locators:
        try:
            # Check for element presence in DOM
            if page.locator(selector).count() > 0: 
                is_challenge_text = True
                break
        except Exception:
            pass
            
    current_url = page.url

    logger.debug(
        "Cloudflare status title=%r URL=%r Challenge Indicator=%s",
        title,
        current_url,
        is_challenge_text,
    )

    if "Access Denied" in title or "Forbidden" in title:
        return "BLOCKED"
    
    if is_challenge_text or "DDOS-GUARD" in title.upper() or "CHECKING YOUR BROWSER" in title.upper():
        return "CHALLENGED"
    
    return "SUCCESS"

def resolve_slow_download_link(url: str, timeout: int) -> Optional[str]:
    """
    Uses the stealth browser to navigate a slow_download page, solve the
    challenge, and extract the final, direct download URL (e.g., momot.rs).

    Returns:
        The final direct download URL string, or None if challenge fails or
        link extraction fails.
    """
    with launch_stealth_browser(DEFAULT_BROWSER) as browser:
        # Use a random user agent from the pool for context
        user_agent = _next_user_agent()
        
        # Configure the browser context for stealth operations
        context = browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080}
        )
        # Apply stealth to the context
        page = context.new_page()

        try:
            # Load the page and wait for initial network activity to settle
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            
            start_time = time.time()
            status = _check_cloudflare_status(page) # Initial check

            logger.debug("Initial Cloudflare check for %s: %s", url, status)

            # Wait for challenge resolution (using timeout from settings)
            while (time.time() - start_time) < (timeout - 5) and status == "CHALLENGED":
                # Check status title every 3 seconds
                time.sleep(3) 
                status = _check_cloudflare_status(page)
                if status in {"SUCCESS", "BLOCKED"}:
                    logger.debug("Network settled after challenge resolution.")
                    break
            
            # --- Link Extraction Logic (Only if SUCCESS) ---
            if status == "SUCCESS":
                logger.info("Challenge solved for %s", url)
                # Give the page a moment to fully render after the redirect/challenge completion
                page.wait_for_load_state("networkidle", timeout=10000) 
                
                # Selector for the final direct download link
                DOWNLOAD_LINK_SELECTOR = "a[href*='momot.rs'], a[href*='cloudflare-ipfs.com'], a[href*='api.annas-archive.org/slow_download']"

                try:
                    # Wait for the specific download link element to be available
                    link_element = page.wait_for_selector(
                        DOWNLOAD_LINK_SELECTOR, 
                        timeout=5000, 
                        state="attached" 
                    ) 
                    
                    # Extract the href attribute
                    download_link = link_element.get_attribute("href")
                    
                    if download_link:
                        logger.debug("Successfully extracted download link: %s", download_link)
                        # CRITICAL: Return the final URL string directly
                        return download_link 
                    else:
                        logger.warning("Found anchor element but no href attribute.")

                except TimeoutError:
                    # Log a warning that link extraction failed
                    logger.warning(
                        "Timed out waiting (5s) for download link selector after challenge success."
                    )
                
                return None # Failed to extract the final URL

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
            
def solve_cloudflare_challenge(
    url: str,
    timeout: int = 90,
    wait_seconds: int = 90,
    browser_type: str = DEFAULT_BROWSER,
) -> Optional[str]:
    """
    Run a stealth Playwright session to wait out Cloudflare/DDoS-Guard pages.

    Returns the direct download URL (str) on successful link extraction for
    slow_download pages, or the page HTML (str) for non-download pages once the
    challenge is cleared. None is returned on permanent failure/timeout.
    """

    user_agent = _next_user_agent()
    logger.info(
        "Attempting Cloudflare bypass for %s with user agent %s", url, user_agent
    )
    
    with Stealth().use_sync(sync_playwright()) as p: 
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")

        # Launch browser with anti-detection arguments and HEADLESS=FALSE
        browser = launcher.launch(
            headless=False, 
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # New context with rotating user agent
        context = browser.new_context(
            user_agent=user_agent, viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        try:
            # Load the page and wait for initial network activity to settle
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000) 
            
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

                # --- STEP 1: Wait for network activity to settle (Aggressive) ---
                try:
                    # Wait for network idle state after challenge is solved (up to 5 seconds)
                    page.wait_for_load_state("networkidle", timeout=5000)
                    logger.debug("Network settled after challenge resolution.")
                except TimeoutError:
                    logger.debug("Network did not settle within 5 seconds, proceeding aggressively.")

                is_download_page = "slow_download" in url

                if is_download_page:
                    # --- STEP 2a: Aggressive Wait, Extract, and Return Link ---
                    DOWNLOAD_LINK_SELECTOR = 'xpath=/html/body/main/div/p[3]/a'

                    try:
                        link_element = page.wait_for_selector(
                            DOWNLOAD_LINK_SELECTOR,
                            timeout=10000,
                            state="attached"
                        )
                        download_link = link_element.get_attribute("href")
                        if download_link:
                            logger.debug("Successfully extracted download link: %s", download_link)
                            return download_link
                        logger.warning("Found anchor element but no href attribute.")
                    except TimeoutError:
                        logger.warning(
                            "Timed out waiting (5s) for download link selector. Capturing current content."
                        )

                    return page.content()

                # --- STEP 2b: Non-download pages (e.g., search) ---
                try:
                    page.wait_for_selector("table", timeout=7000)
                except TimeoutError:
                    logger.debug(
                        "Search/result page did not expose a <table> within timeout; returning current content."
                    )
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
