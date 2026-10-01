#!/usr/bin/env python3
"""Debug script to check progress bars in headless mode."""
import time
import sys
from stealth_browser import launch_stealth_browser

def debug_progress_bars():
    """Launch browser in headless mode and inspect progress bar state."""
    print("🔍 Launching headless browser to debug progress bars...")
    
    with launch_stealth_browser(headless=True) as browser:
        context = browser.new_context()
        page = context.new_page()
        
        # Navigate to home page
        print("📡 Navigating to http://localhost:5000/")
        page.goto("http://localhost:5000/", wait_until="domcontentloaded", timeout=30000)
        
        # Wait for page to fully load and SSE to connect
        print("⏳ Waiting for SSE connection and page stabilization...")
        time.sleep(3)
        
        # Check if progress bar elements exist and are visible
        print("\n🔍 Checking for progress bar elements...")
        
        # Check navbar exists
        navbar = page.query_selector('nav.navbar')
        print(f"✓ nav.navbar exists: {navbar is not None}")
        
        metadata_container = page.query_selector('#metadata-progress-container')
        print(f"✓ #metadata-progress-container exists: {metadata_container is not None}")
        
        if metadata_container:
            # Get computed styles
            display = page.evaluate('el => window.getComputedStyle(el).display', metadata_container)
            visibility = page.evaluate('el => window.getComputedStyle(el).visibility', metadata_container)
            opacity = page.evaluate('el => window.getComputedStyle(el).opacity', metadata_container)
            print(f"  - display: {display}")
            print(f"  - visibility: {visibility}")
            print(f"  - opacity: {opacity}")
        
        metadata_wrapper = page.query_selector('#metadata-progress-wrapper')
        print(f"\n✓ #metadata-progress-wrapper exists: {metadata_wrapper is not None}")
        
        if metadata_wrapper:
            # Check styles
            display = page.evaluate('el => window.getComputedStyle(el).display', metadata_wrapper)
            visibility = page.evaluate('el => window.getComputedStyle(el).visibility', metadata_wrapper)
            height = page.evaluate('el => window.getComputedStyle(el).height', metadata_wrapper)
            has_active = page.evaluate('el => el.classList.contains("active")', metadata_wrapper)
            print(f"  - display: {display}")
            print(f"  - visibility: {visibility}")
            print(f"  - height: {height}")
            print(f"  - has 'active' class: {has_active}")
        
        # Check the fill bar
        metadata_fill = page.query_selector('#metadata-progress-fill')
        print(f"\n✓ #metadata-progress-fill exists: {metadata_fill is not None}")
        
        if metadata_fill:
            width = page.evaluate('el => el.style.width', metadata_fill)
            computed_width = page.evaluate('el => window.getComputedStyle(el).width', metadata_fill)
            print(f"  - inline width: {width}")
            print(f"  - computed width: {computed_width}")
        
        # Get text content
        print("\n📝 Progress bar text content:")
        
        metadata_percent = page.query_selector('#metadata-progress-percent')
        if metadata_percent:
            text = (metadata_percent.text_content() or '').strip()
            print(f"  - percent: '{text}'")
        
        metadata_label = page.query_selector('#metadata-progress-label')
        if metadata_label:
            text = (metadata_label.text_content() or '').strip()
            print(f"  - label: '{text}'")
        
        metadata_book = page.query_selector('#metadata-progress-book')
        if metadata_book:
            text = (metadata_book.text_content() or '').strip()
            print(f"  - book: '{text}'")
        
        metadata_step = page.query_selector('#metadata-progress-step')
        if metadata_step:
            text = (metadata_step.text_content() or '').strip()
            print(f"  - step: '{text}'")
        
        metadata_eta = page.query_selector('#metadata-progress-eta')
        if metadata_eta:
            text = (metadata_eta.text_content() or '').strip()
            print(f"  - eta: '{text}'")
        
        # Check if EventSource is connected
        print("\n🔗 Checking EventSource connection in page context...")
        
        es_info = page.evaluate('''() => {
            // Try to find the EventSource object
            if (!window.es) {
                return { exists: false, error: "window.es not found" };
            }
            
            const es = window.es;
            const readyStates = ['CONNECTING', 'OPEN', 'CLOSED'];
            
            return {
                exists: true,
                readyState: es.readyState,
                readyStateLabel: readyStates[es.readyState] || 'UNKNOWN',
                url: es.url || 'unknown'
            };
        }''')
        
        print(f"  - EventSource exists: {es_info.get('exists', False)}")
        print(f"  - Ready state: {es_info.get('readyStateLabel', 'N/A')} ({es_info.get('readyState', 'N/A')})")
        print(f"  - URL: {es_info.get('url', 'unknown')}")
        
        # Check for errors in page
        print("\n📋 Checking for JavaScript errors...")
        
        page_errors = []
        def on_console_msg(msg):
            if msg.type in ['error', 'warning']:
                page_errors.append(f"[{msg.type.upper()}] {msg.text}")
        
        page.on("console", on_console_msg)
        
        # Wait for some data to potentially come through
        print("\n⏳ Waiting 5 seconds for SSE data...")
        for i in range(5):
            time.sleep(1)
            
            # Check current progress
            progress_info = page.evaluate('''() => {
                const wrapper = document.getElementById('metadata-progress-wrapper');
                const fill = document.getElementById('metadata-progress-fill');
                const percent = document.getElementById('metadata-progress-percent');
                
                return {
                    wrapper_active: wrapper?.classList.contains('active') || false,
                    fill_width: fill?.style.width || '0%',
                    percent_text: percent?.textContent || 'N/A',
                    wrapper_display: wrapper ? window.getComputedStyle(wrapper).display : 'N/A'
                };
            }''')
            
            print(f"  [{i+1}s] Active: {progress_info['wrapper_active']}, " +
                  f"Width: {progress_info['fill_width']}, " +
                  f"Percent: {progress_info['percent_text']}, " +
                  f"Display: {progress_info['wrapper_display']}")
        
        if page_errors:
            print("\n⚠️  Found errors:")
            for error in page_errors:
                print(f"  {error}")
        else:
            print("\n✅ No JavaScript errors detected")
        
        # Final state
        print("\n📊 Final progress bar state:")
        final_state = page.evaluate('''() => {
            const wrapper = document.getElementById('metadata-progress-wrapper');
            const fill = document.getElementById('metadata-progress-fill');
            const percent = document.getElementById('metadata-progress-percent');
            const book = document.getElementById('metadata-progress-book');
            
            return {
                wrapper_visible: wrapper ? window.getComputedStyle(wrapper).display !== 'none' : false,
                wrapper_active: wrapper?.classList.contains('active') || false,
                fill_width: fill?.style.width || '0%',
                percent_text: percent?.textContent?.trim() || '--',
                book_text: book?.textContent?.trim() || '--'
            };
        }''')
        
        for key, val in final_state.items():
            print(f"  {key}: {val}")
        
        context.close()
        
        print("\n✅ Debug complete!")
        return final_state

if __name__ == "__main__":
    try:
        result = debug_progress_bars()
        
        # Print conclusion
        if result['wrapper_active']:
            print("\n🟢 PROGRESS BARS ARE ACTIVE AND UPDATING")
        elif result['wrapper_visible']:
            print("\n🟡 PROGRESS BARS ARE VISIBLE BUT NOT ACTIVE")
        else:
            print("\n🔴 PROGRESS BARS ARE NOT VISIBLE")
            print("\nPossible issues:")
            print("  1. Navbar container not rendering")
            print("  2. EventSource not connected")
            print("  3. SSE data not being sent")
            print("  4. CSS hiding the bars")
            
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
