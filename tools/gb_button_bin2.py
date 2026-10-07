#!/usr/bin/env python3
"""Does The Button Bin have REAL chapter structure that we are missing?
Both questions in one probe, using the file's own bytes:
  1. does the .mobi's own TOC ( compiler -e / calibre metadata ) list chapters?
  2. the text uses '*' scene separators — is that ALL the structure it has?

If the book genuinely has no chapters (a single 31k-char short story is
plausible — this is in the Lorenzo folder, a kid's folder, but Mike Allen writes
adult horror), then 1 chapter is CORRECT and the fix is elsewhere.
"""
import subprocess, sys, re
from pathlib import Path

sys.path.insert(0, "/usr/local/bin/GoodBooks")

MOBI = Path("/mnt/8tbdas/GoodBooks/Lorenzo/The Button Bin-Allen; Mike.mobi")

print("=== 1. calibre's own view of the mobi (metadata) ===")
r = subprocess.run(["/usr/bin/ebook-meta", str(MOBI)], capture_output=True, text=True)
if r.returncode != 0:
    r2 = subprocess.run(["which", "ebook-meta"], capture_output=True, text=True)
    print("  ebook-meta missing:", r2.stdout.strip() or "not found")
else:
    print("\n".join("  " + l for l in r.stdout.strip().splitlines()[:12]))

print()
print("=== 2. structure census of the converted text ===")
import zipfile
import gb_audiobook as AB
z = zipfile.ZipFile("/tmp/bb.epub")
raw = z.read("index_split_000.html").decode("utf-8", "replace")
txt = AB._html_to_text(raw)
lines = [l.strip() for l in txt.splitlines() if l.strip()]
chars = sum(len(l) for l in lines)
print(f"  non-empty lines: {len(lines)}  total chars: {chars}")
print(f"  median line length: {sorted(len(l) for l in lines)[len(lines)//2]}")

# scene separators
seps = sum(1 for l in lines if set(l.replace(' ', '')) <= {'*'} and l)
print(f"  '*' separators: {seps}  -> {seps//1} potential scene breaks")

# any TOC/anchor structure in the raw html?
anchors = re.findall(r'<a[^>]+name="([^"]+)"', raw)
ids = re.findall(r'id="([^"]+)"', raw)
print(f"  named anchors: {len(anchors)}  element ids: {len(ids)}")
print(f"  anchors: {anchors[:6]}")

# h1-h6 anywhere, even case/typo variants ?
hevery = re.findall(r"<h([1-9])", raw, re.I)
print(f"  any hN tags at all: {hevery[:8]}")
