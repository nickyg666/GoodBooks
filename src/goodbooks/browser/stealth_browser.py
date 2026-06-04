import logging
import os
import random
import re
import time
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import urlparse, urlunparse

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
    """Heuristically detect Cloudflare/anti-bot interstitials.
    
    Returns True if a challenge is detected (should be solved).
    Returns False if it's a rate limit response (403 with invalid/expired token).
    """
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
    
    # Check for rate limit indicators (invalid or expired tokens) - these are NOT solvable challenges
    rate_limit_indicators = [
        "invalid",
        "expired",
        "rate limit",
        "too many requests",
    ]
    if any(indicator in text_lower for indicator in rate_limit_indicators):
        # This is rate limiting, not a challenge
        return False
    
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


def download_binary_with_stealth(url: str, timeout: int = 60, browser_type: str = DEFAULT_BROWSER) -> Optional[bytes]:
    """
    Download a binary file (like EPUB) using Playwright's response interception.
    This captures the actual file content from /get.php redirects by monitoring network responses.
    
    Returns:
        Binary file content as bytes, or None if download fails
    """
    logger.debug("Binary download via stealth browser for url=%s with timeout=%d", url, timeout)
    
    captured_content = None
    
    try:
        with launch_stealth_browser(browser_type, headless=False) as browser:
            context = browser.new_context(user_agent=WINDOWS_USER_AGENT)
            page = context.new_page()
            
            # Set up a response handler to capture binary file responses
            def handle_response(response):
                nonlocal captured_content
                try:
                    # Check if this response is a binary file (EPUB, PDF, MOBI, etc.)
                    content_type = response.headers.get("content-type", "").lower()
                    content_length = response.headers.get("content-length", "0")
                    
                    # Check for binary file content types
                    binary_types = [
                        "application/octet-stream",
                        "application/epub",
                        "application/pdf",
                        "application/x-mobipocket-ebook",
                        "application/gzip",
                    ]
                    
                    is_binary = any(btype in content_type for btype in binary_types) or int(content_length or 0) > 100000
                    
                    if is_binary and response.status < 400:
                        logger.debug("Captured binary response: %s (%s, %s bytes)", response.url, content_type, content_length)
                        try:
                            captured_content = response.body()
                        except Exception as e:
                            logger.debug("Failed to capture response body: %s", str(e))
                except Exception as e:
                    logger.debug("Error in response handler: %s", str(e))
            
            page.on("response", handle_response)
            
            try:
                logger.debug("Navigating to %s to capture file download", url)
                page.goto(url, timeout=timeout * 1000, wait_until="load")
            except Exception as e:
                logger.debug("Navigation resulted in: %s", type(e).__name__)
            
            # Wait a bit for any pending downloads/responses
            page.wait_for_timeout(1000)
            
            context.close()
            
            if captured_content:
                logger.debug("Binary download successful: %d bytes from %s", len(captured_content), url)
                return captured_content
            else:
                logger.warning("No binary content captured from %s", url)
                return None
                
    except Exception as e:
        logger.error("Binary download failed for %s: %s", url, str(e))
        return None


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
    except (TimeoutError, Exception):
        # Page context might be destroyed during navigation
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
            # Use a short timeout to avoid hanging if context is destroyed
            if page.locator(selector).count() > 0: 
                is_challenge_text = True
                break
        except Exception:
            # Ignore any errors (context destroyed, timeout, etc.)
            pass
    
    try:        
        current_url = page.url
    except Exception:
        # If we can't get the URL, assume we're still navigating
        current_url = "URL_FETCH_FAILED"

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
    logger.info("resolve_slow_download_link: Starting stealth browser for %s (timeout=%d)", url, timeout)
    try:
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
                logger.info("resolve_slow_download_link: Navigating to %s", url)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                logger.info("resolve_slow_download_link: Navigation complete, checking Cloudflare status")
                
                start_time = time.time()
                try:
                    status = _check_cloudflare_status(page) # Initial check
                except Exception as e:
                    logger.debug("Error during initial Cloudflare check: %s", e)
                    status = "CHALLENGED"

                logger.info("Initial Cloudflare check for %s: %s", url, status)

                # Wait for challenge resolution (using timeout from settings)
                # Check status every 1 second until timeout
                while (time.time() - start_time) < timeout and status == "CHALLENGED":
                    # Check status title every 2 seconds
                    time.sleep(1)
                    try:
                        status = _check_cloudflare_status(page)
                    except Exception as e:
                        logger.debug("Error checking Cloudflare status in loop: %s", e)
                        # Continue waiting if we can't check status
                        continue
                        
                    if status in {"SUCCESS", "BLOCKED"}:
                        logger.info("Challenge status changed to %s after %.1f seconds", status, time.time() - start_time)
                        break
                
                # If still challenged after timeout, give up
                if status == "CHALLENGED":
                    logger.warning("Challenge not resolved after %.1f seconds timeout", time.time() - start_time)
                    return None
                
                # --- Link Extraction Logic (Only if SUCCESS) ---
                if status == "SUCCESS":
                    logger.info("Challenge solved for %s", url)
                    
                    # Check if this is a direct momot.rs URL that might be returning 403
                    # If we're navigating to a file directly, the browser may show 403
                    # In that case, return the URL itself as the download link
                    if "momot.rs" in url and "slow_download" not in url:
                        logger.info("Direct momot.rs URL detected, returning it as-is (browser will handle 403)")
                        return url
                    
                    # Give the page a moment to fully render after the redirect/challenge completion
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception as e:
                        logger.debug("Network did not settle: %s", e)
                    
                    # Try multiple selectors for the download link (in order of preference)
                    download_link_selectors = [
                        # Primary: Anna's Archive direct link button
                        "a[href*='momot.rs']",
                        "a[href*='cloudflare-ipfs.com']",
                        # Fallback: Anna's Archive CDN
                        "a[href*='api.annas-archive.org']",
                        # LibGen fallback: GET link to download
                        "a[href*='/get.php']",
                        "a[href*='get.php']",
                        # Generic fallback: any link with download or file extension
                        "a[href*='.azw'], a[href*='.epub'], a[href*='.mobi'], a[href*='.pdf']",
                        # Last resort: the first link in the main content
                        "main a, article a, [role='main'] a, table a",
                    ]

                    download_link = None
                    for selector in download_link_selectors:
                        try:
                            # Wait for the download link element to be available
                            link_element = page.wait_for_selector(
                                selector, 
                                timeout=3000,  # Shorter timeout per selector
                                state="attached" 
                            ) 
                            
                            # Extract the href attribute
                            download_link = link_element.get_attribute("href")
                            
                            if download_link:
                                # Convert relative URLs to absolute
                                if download_link.startswith("/"):
                                    # Get the base URL from the current page
                                    page_url = page.url
                                    parsed = urlparse(page_url)
                                    download_link = urlunparse((parsed.scheme, parsed.netloc, download_link, "", "", ""))
                                
                                logger.info(
                                    "Successfully extracted download link with selector '%s': %s",
                                    selector,
                                    download_link
                                )
                                return download_link

                        except Exception as e:
                            logger.debug("Selector '%s' failed: %s", selector, e)
                            continue
                    
                    # If we got here, no selector worked - try fallback: search page content for momot.rs
                    logger.info("No selectors worked, attempting fallback search for momot.rs link in page content")
                    try:
                        page_content = page.content()
                        # Search for momot.rs URLs in the page HTML
                        momot_match = re.search(r'href=["\']?(https?://[^"\'\s<>]+momot\.rs[^"\'\s<>]*)["\']?', page_content)
                        if momot_match:
                            fallback_url = momot_match.group(1)
                            logger.info("Found momot.rs fallback URL in page: %s", fallback_url)
                            return fallback_url
                    except Exception as e:
                        logger.debug("Fallback search for momot.rs failed: %s", e)
                    
                    logger.warning(
                        "Could not find download link with any selector or fallback after challenge success for %s",
                        url
                    )
                    return None

                if status == "BLOCKED":
                    logger.warning("Access permanently blocked to %s", url)
                    return None

                logger.warning("Timed out waiting for challenge to resolve for %s after %.1f seconds", url, time.time() - start_time)
                return None
            
            except TimeoutError as e:
                logger.error("Navigation or wait timed out for %s: %s", url, e)
                return None
                
            finally:
                browser.close()
    except Exception as e:
        logger.exception("Unexpected error in resolve_slow_download_link for %s", url)
        return None
            
def solve_cloudflare_challenge(
    url: str,
    timeout: int = 10, 
    wait_seconds: int = 10, 
    browser_type: str = DEFAULT_BROWSER,
) -> Optional[str]:
    """
    Run a stealth Playwright session to wait out Cloudflare/DDoS-Guard pages.
    
    Returns the direct download URL (str) on successful link extraction, 
    the page content (str HTML) if link extraction fails, or None on permanent failure/timeout.
    """
    
    # Goodreads doesn't require Cloudflare bypass - skip browser automation
    if "goodreads.com" in url.lower():
        logger.debug("Skipping Cloudflare detection for Goodreads URL: %s", url)
        return None

    user_agent = _next_user_agent()
    logger.info(
        "Attempting Cloudflare bypass for %s with user agent %s", url, user_agent
    )
    
    with Stealth().use_sync(sync_playwright()) as p: 
        launcher = getattr(p, browser_type, None)
        if launcher is None:
            raise ValueError(f"Unsupported Playwright browser type: {browser_type}")

        # Launch browser with anti-detection arguments and HEADLESS=FALSE (with xvfb-run wrapper for display)
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
                try:
                    status = _check_cloudflare_status(page)
                except Exception as e:
                    logger.debug("Error checking Cloudflare status: %s", e)
                    status = "CHALLENGED"  # Assume still challenged if we can't check
                
                if status in {"SUCCESS", "BLOCKED"}:
                    break
                # Wait only 1 second
                time.sleep(1) 

            if status == "SUCCESS":
                logger.info("Challenge solved for %s", url)
                
                # --- STEP 1: Wait for network activity to settle (Aggressive) ---
                try:
                    # Wait for network idle state after challenge is solved (up to 5 seconds)
                    page.wait_for_load_state("networkidle", timeout=5000) 
                    logger.debug("Network settled after challenge resolution.")
                except Exception as e:
                    logger.debug("Network did not settle within 5 seconds, proceeding aggressively: %s", e)

                # --- STEP 2: Aggressive Wait, Extract, and Return Link ---
                # Try multiple selectors for the download link (in order of preference)
                download_link_selectors = [
                    # Primary: Anna's Archive direct link button
                    "a[href*='momot.rs']",
                    "a[href*='cloudflare-ipfs.com']",
                    # Fallback: Anna's Archive CDN
                    "a[href*='api.annas-archive.org']",
                    # LibGen fallback: GET link to download
                    "a[href*='/get.php']",
                    "a[href*='get.php']",
                    # Generic fallback: any link with download or file extension
                    "a[href*='.azw'], a[href*='.epub'], a[href*='.mobi'], a[href*='.pdf']",
                    # Last resort: the first link in the main content
                    "main a, article a, [role='main'] a, table a",
                ]
                
                download_link = None
                for selector in download_link_selectors:
                    try:
                        # Wait for selector and get the element
                        link_element = page.wait_for_selector(
                            selector, 
                            timeout=3000,  # Shorter timeout per selector
                            state="attached" 
                        ) 
                        
                        # Extract the href attribute
                        download_link = link_element.get_attribute("href")
                        
                        if download_link:
                            # Convert relative URLs to absolute
                            if download_link.startswith("/"):
                                # Get the base URL from the current page
                                page_url = page.url
                                parsed = urlparse(page_url)
                                download_link = urlunparse((parsed.scheme, parsed.netloc, download_link, "", "", ""))
                            
                            logger.debug(
                                "Successfully extracted download link with selector '%s': %s",
                                selector,
                                download_link
                            )
                            return download_link

                    except Exception as e:
                        logger.debug("Selector '%s' failed: %s", selector, e)
                        continue
                
                # If we got here, no selector worked
                logger.warning(
                    "Could not find download link with any selector. "
                    "Capturing current page content."
                )
                
                # Fallback: Capture and return the HTML content if link extraction fails
                try:
                    return page.content()
                except Exception as e:
                    logger.error("Failed to get page content: %s", e)
                    return None

            if status == "BLOCKED":
                logger.warning("Access permanently blocked to %s", url)
                return None

            logger.warning("Timed out waiting for challenge to resolve for %s", url)
            return None
        
        except TimeoutError as e:
            logger.error("Navigation or wait timed out for %s: %s", url, e)
            return None
        except Exception as e:
            logger.error("Unexpected error during Cloudflare bypass for %s: %s", url, e)
            return None


def download_file_with_stealth(url: str, timeout: int = 10) -> Optional[bytes]:
    """
    Use stealth browser to solve Cloudflare challenge for a direct file URL,
    then download the file directly using the browser's session/cookies.
    
    Returns:
        File bytes if successful, None otherwise.
    """
    with launch_stealth_browser(DEFAULT_BROWSER) as browser:
        user_agent = _next_user_agent()
        context = browser.new_context(
            user_agent=user_agent,
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        try:
            # Navigate to the URL to solve any Cloudflare challenge
            logger.debug("Navigating to %s to solve Cloudflare challenge", url)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            
            # Check if there's a Cloudflare challenge
            start_time = time.time()
            try:
                status = _check_cloudflare_status(page)
            except Exception:
                status = "CHALLENGED"

            # Wait for challenge resolution
            while (time.time() - start_time) < timeout and status == "CHALLENGED":
                time.sleep(1)
                try:
                    status = _check_cloudflare_status(page)
                except Exception:
                    continue
                if status in {"SUCCESS", "BLOCKED"}:
                    break
            
            if status == "BLOCKED":
                logger.warning("Access permanently blocked to %s", url)
                return None
            
            if status == "CHALLENGED":
                logger.warning("Cloudflare challenge timed out for %s", url)
                return None
            
            # Challenge solved - now download the file using the browser's session
            logger.info("Challenge solved, downloading file from %s", url)
            
            # Use the browser's fetch API to download the file
            # This preserves cookies and headers from the challenge solution
            try:
                with page.expect_download() as download_info:
                    # Trigger a download by navigating to the URL within the page context
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                
                download = download_info.value
                # Read the downloaded file content
                file_bytes = download.path().read_bytes()
                logger.info("Successfully downloaded file from %s (%d bytes)", url, len(file_bytes))
                return file_bytes
            
            except Exception as e:
                logger.debug("Browser download failed, trying direct fetch: %s", e)
                # Fallback: use the browser's fetch capability through JavaScript
                try:
                    response_data = page.evaluate("""
                        async () => {
                            const response = await fetch(window.location.href);
                            const blob = await response.blob();
                            const arrayBuffer = await blob.arrayBuffer();
                            return Array.from(new Uint8Array(arrayBuffer));
                        }
                    """)
                    file_bytes = bytes(response_data)
                    logger.info("Successfully fetched file via JavaScript (%d bytes)", len(file_bytes))
                    return file_bytes
                except Exception as e2:
                    logger.error("Both download methods failed for %s: %s, %s", url, e, e2)
                    return None
        
        except TimeoutError as e:
            logger.error("Navigation timed out for %s: %s", url, e)
            return None
        except Exception as e:
            logger.error("Unexpected error during stealth download for %s: %s", url, e)
            return None
        finally:
            try:
                context.close()
            except Exception:
                pass

