#!/usr/bin/env python3
"""Quick test to see what Anna's Archive returns for a search."""
import requests
from lxml import html
from urllib.parse import urlencode

query = "the hobbit"
params = [
    ("q", query),
    ("display", "table"),
    ("lang", "en"),
    ("page", "1"),
    ("index", ""),
    ("sort", ""),
]

url = f"https://annas-archive.org/search?{urlencode(params, doseq=True)}"
print(f"Fetching: {url}")
print("="*80)

try:
    resp = requests.get(url, timeout=10, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    print(f"Status: {resp.status_code}")
    print(f"Content Length: {len(resp.content)} bytes")
    
    tree = html.fromstring(resp.content)
    
    # Try to find all table rows
    all_rows = tree.xpath("//table//tr[td]")
    print(f"\nTotal rows with <td>: {len(all_rows)}")
    
    if all_rows:
        print("\nFirst row structure:")
        first_row = all_rows[0]
        cols = first_row.findall("td")
        print(f"  Number of columns: {len(cols)}")
        for i, col in enumerate(cols):
            text = "".join(col.xpath(".//text()")).strip()[:100]
            print(f"  Col {i}: {text}")
    else:
        print("\nNo rows found! Let's check what tables exist:")
        tables = tree.xpath("//table")
        print(f"Total tables: {len(tables)}")
        if tables:
            first_table = tables[0]
            print(f"First table class: {first_table.get('class')}")
            print(f"First table id: {first_table.get('id')}")
            # Try to find all tr elements (even without td check)
            all_trs = first_table.xpath(".//tr")
            print(f"Total TR elements in first table: {len(all_trs)}")
            if all_trs:
                print(f"First TR has {len(all_trs[0].findall('td'))} <td> and {len(all_trs[0].findall('th'))} <th>")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
