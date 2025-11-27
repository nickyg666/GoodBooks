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
    """Inspect the page for Cloudflare / DDoS-Guard style interstitials.

    Returns one of:
      * "CHALLENGED" – still seeing a challenge / captcha / DDOS-Guard page.
      * "BLOCKED"    – hard block (403 / Access Denied).
      * "SUCCESS"    – appears to be through to the real page.
    """
    try:
        title = page.title()
    except TimeoutError:
        # Playwright timed out waiting for the title – treat as "still challenged".
        logger.debug("Timed out while fetching page.title() during Cloudflare check")
        title = "TITLE_FETCH_TIMEOUT"
    except Exception as exc:
        # This is the one you're hitting:
        # playwright._impl._errors.Error: Execution context was destroyed, most likely because of a navigation
        # i.e. the page is in the middle of a navigation. Don't crash the whole resolver for that.
        logger.debug(
            "Error while fetching page.title() during Cloudflare check: %s",
            exc,
            exc_info=True,
        )
        title = "TITLE_FETCH_ERROR"

    is_challenge_text = False

    # Check for common challenge texts (Cloudflare / DDOS-Guard / captcha)
    challenge_locators = [
        "text=Checking your browser before accessing",
        "text=DDOS-GUARD",
        'iframe[src*="captcha"]',
        'iframe[src*="turnstile"]',
    ]

    for selector in challenge_locators:
        try:
            if page.locator(selector).count() > 0:
                is_challenge_text = True
                break
        except Exception:
            # If the DOM is in flux due to navigation, just ignore and rely on title.
            continue

    try:
        current_url = page.url
    except Exception:
        current_url = "<unavailable>"

    logger.debug(
        "Cloudflare status title=%r URL=%r Challenge Indicator=%s",
        title,
        current_url,
        is_challenge_text,
    )

    # Hard block – usually not worth retrying this URL in this session
    if "Access Denied" in title or "Forbidden" in title:
        return "BLOCKED"

    # Still looks like a challenge page, or we couldn't reliably read the title
    if (
        is_challenge_text
        or "DDOS-GUARD" in title.upper()
        or "CHECKING YOUR BROWSER" in title.upper()
        or "TITLE_FETCH_ERROR" in title
    ):
        return "CHALLENGED"

    # Otherwise assume we're through to the real page
    return "SUCCESS"

def solve_cloudflare_challenge(
    url: str,
    timeout: int = 90, 
    wait_seconds: int = 90, 
    browser_type: str = DEFAULT_BROWSER,
) -> Optional[str]:
    """
    Run a stealth Playwright session to wait out Cloudflare/DDoS-Guard pages.
    
    Returns the direct download URL (str) on successful link extraction, 
    the page content (str HTML) if link extraction fails, or None on permanent failure/timeout.
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

                # --- STEP 2: Aggressive Wait, Extract, and Return Link ---
                DOWNLOAD_LINK_SELECTOR = 'xpath=/html/body/main/div/p[3]/a'
                
                try:
                    # Wait for selector and get the element
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
                    # Log a warning but capture the content anyway, as the page might have loaded
                    logger.warning(
                        "Timed out waiting (5s) for download link selector. Capturing current content."
                    )
                
                # Fallback: Capture and return the HTML content if link extraction fails
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
