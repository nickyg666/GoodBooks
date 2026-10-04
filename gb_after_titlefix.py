#!/usr/bin/env python3
"""After the title repair: did the Firestarter/Misery false merge split?

That merge existed because both records stored the title "Stephen King" --
the author's name in the title field. With the title repaired from each
file's own dc:title, the two novels should now key apart.

Also re-measures coverage and cluster counts so the effect is quantified
rather than asserted.
"""
import collections
import json
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
d = json.loads((ROOT / "data" / "library_metadata.json").read_text())


def real(k):
    return LIB / k.split("::", 1)[-1]


print("=" * 72)
print("1. THE FALSE MERGE")
print("=" * 72)
for k, v in d.items():
    p = real(k)
    if not p.exists():
        continue
    if "Firestarter" in p.name or "Misery" in p.name:
        print(f"  {p.name[:46]:48} title={str(v.get('title'))[:26]!r}")
        print(f"     book_key: {v.get('book_key')}")

keys = {}
for k, v in d.items():
    keys.setdefault(v.get("book_key"), []).append(k)
merged = keys.get("stephen king|stephen king", [])
print()
print(f"  still merged under 'stephen king|stephen king': {len(merged)}")

print()
print("=" * 72)
print("2. TITLE QUALITY AFTER THE REPAIR")
print("=" * 72)
titles = [(v.get("title") or "").strip() for v in d.values()
          if isinstance(v, dict)]
print(f"  records                : {len(titles)}")
print(f"  empty                  : {sum(1 for t in titles if not t)}")
print(f"  over 100 chars         : {sum(1 for t in titles if len(t) > 100)}"
      "   (was 908)")
print(f"  over 12 words          : {sum(1 for t in titles if len(t.split()) > 12)}"
      "   (was 1202)")
print(f"  longest                : {max(len(t) for t in titles)} chars"
      "   (was 251)")

print()
print("=" * 72)
print("3. BOOK KEYS AFTER THE REPAIR")
print("=" * 72)
real_keys = collections.Counter(
    v.get("book_key") for k, v in d.items()
    if isinstance(v, dict) and real(k).exists())
multi = {k: c for k, c in real_keys.items() if c > 1}
print(f"  files with a key       : {sum(real_keys.values())}")
print(f"  distinct book_keys     : {len(real_keys)}")
print(f"  keys holding >1 file   : {len(multi)}"
      f"  (was 94)")
print(f"  redundant files        : {sum(multi.values()) - len(multi)}"
      "   (was 98)")
print()
print("  largest clusters:")
for k, c in sorted(multi.items(), key=lambda kv: -kv[1])[:6]:
    print(f"    x{c:<3} {k[:60]}")
