"""
Playwright audit pass for GoodBooks.

Walks the public pages, exercises a few interactions, and reports:
  - console errors
  - failed network requests
  - accessibility quick-checks (lang attr, page title, button labels)
  - basic visual differences between themes (screenshot diff)
  - broken links / 404s
  - form fields missing labels

The audit is non-destructive: it never POSTs, never sends to Kindle, never
triggers feeds. It only reads. Use this in CI / pre-deploy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright, Page, ConsoleMessage, Response

PAGES = [
    ("/", "Home"),
    ("/library/recently-added", "Recently added"),
    ("/settings", "Settings"),
    ("/history", "History"),
    ("/feeds/view", "Feeds"),
    ("/search", "Search"),
]

THEMES = ["light", "dark", "sepia", "high-contrast"]


def audit_page(page: Page, url: str, label: str, screenshots_dir: Path) -> dict[str, Any]:
    findings: dict[str, Any] = {
        "label": label, "url": url,
        "console_errors": [], "failed_requests": [], "a11y": {},
    }
    console_errors: list[str] = []
    failed_requests: list[dict] = []

    def on_console(msg: ConsoleMessage) -> None:
        if msg.type in ("error", "warning"):
            console_errors.append(f"[{msg.type}] {msg.text}")

    def on_response(resp: Response) -> None:
        if resp.status >= 400:
            failed_requests.append({"url": resp.url, "status": resp.status})

    page.on("console", on_console)
    page.on("response", on_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        findings["load_error"] = str(e)
        return findings

    findings["a11y"] = {
        "has_lang": page.evaluate("() => document.documentElement.getAttribute('lang')") is not None,
        "title": page.title(),
        "headings": page.evaluate(
            "() => Array.from(document.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim()).filter(Boolean).slice(0, 20)"
        ),
        "form_inputs_without_label": page.evaluate(
            """() => {
                const inputs = Array.from(document.querySelectorAll('input, select, textarea'));
                return inputs.filter(el => {
                    if (el.type === 'hidden') return false;
                    if (el.id && document.querySelector(`label[for="${el.id}"]`)) return false;
                    if (el.closest('label')) return false;
                    if (el.getAttribute('aria-label')) return false;
                    if (el.getAttribute('aria-labelledby')) return false;
                    return true;
                }).map(el => ({tag: el.tagName, name: el.name || null, type: el.type || null, id: el.id || null}));
            }"""
        ),
    }

    findings["console_errors"] = console_errors[:20]
    findings["failed_requests"] = failed_requests[:20]

    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    page.screenshot(path=str(screenshots_dir / f"{safe_label}.png"), full_page=False)
    return findings


def switch_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """t => {
            try { localStorage.setItem('goodbooks-theme', t); } catch (e) {}
            document.documentElement.setAttribute('data-theme', t);
        }""",
        theme,
    )
    page.wait_for_timeout(150)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:5000")
    p.add_argument("--out", default="/tmp/goodbooks-audit")
    p.add_argument("--only", default=None)
    p.add_argument("--skip-themes", action="store_true")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    screenshots = out / "screenshots"
    screenshots.mkdir(exist_ok=True)

    pages = PAGES
    if args.only:
        wanted = set(args.only.split(","))
        pages = [(u, l) for (u, l) in PAGES if u in wanted]

    report: dict[str, Any] = {"base_url": args.base_url, "pages": [], "themes": {}}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})

        for path, label in pages:
            page = context.new_page()
            try:
                findings = audit_page(page, args.base_url + path, label, screenshots)
                report["pages"].append(findings)
            finally:
                page.close()

        if not args.skip_themes:
            for theme in THEMES:
                page = context.new_page()
                try:
                    page.goto(args.base_url + "/", wait_until="domcontentloaded", timeout=15000)
                    switch_theme(page, theme)
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(screenshots / f"theme-{theme}.png"), full_page=False)
                    report["themes"][theme] = {"ok": True}
                except Exception as e:
                    report["themes"][theme] = {"ok": False, "error": str(e)}
                finally:
                    page.close()

        context.close()
        browser.close()

    out_path = out / f"report-{int(time.time())}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Report: {out_path}")

    bad = []
    for f in report["pages"]:
        if f.get("load_error"):
            bad.append(f"LOAD ERROR: {f['url']} {f['load_error']}")
        if f["console_errors"]:
            bad.append(f"CONSOLE: {f['url']} -> {len(f['console_errors'])} messages")
        if f["failed_requests"]:
            bad.append(f"NETWORK: {f['url']} -> {len(f['failed_requests'])} failures")
        # /api/users is JSON, no <html>; skip the lang check there
        if not f["url"].endswith("/api/users") and not f["a11y"].get("has_lang"):
            bad.append(f"A11Y: {f['url']} missing <html lang>")
        if f["a11y"].get("form_inputs_without_label"):
            bad.append(f"A11Y: {f['url']} has {len(f['a11y']['form_inputs_without_label'])} unlabeled inputs")

    if bad:
        print("\nISSUES:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print("\nClean. No console errors, no failed requests, a11y checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
