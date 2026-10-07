#!/usr/bin/env python3
"""Dig for better evidence on the 60 + 8 records the first repair skipped.

Class B2 (60): stored title is junk AND dc:title is junk ('upload', generic
words, filename echoes). The first repair left them alone because the only
title in the file was worthless.

Class A (8): the stored title IS the author. Some were repaired from
dc:title; the remainder are not epubs, so there was no dc:title to read.

Before concluding these are unfixable, exhaust what a book file can still
tell us, because more evidence exists than dc:title:

  1. every dc:* field in the OPF (creator, publisher, date, identifier,
     description, subject, rights) -- a good dc:creator plus a junk title is
     still a usable pair;
  2. <title> and the first <h1> in the first spine document, which is
     frequently the real book title when dc:title is 'upload';
  3. an ISBN in the OPF, which can be resolved against OpenLibrary -- and
     OpenLibrary is REACHABLE from this host, unlike Goodreads which is
     behind an AWS WAF challenge. That is the strongest external evidence
     available and it was never tried;
  4. the filename, as a last resort, since the library convention is
     <Title>-<Author>.

READ ONLY. Nothing is written.
"""
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())

sys.path.insert(0, str(ROOT))
import gb_authors as GA  # noqa: E402

JUNK = {
    "upload", "uploaded", "untitled", "unknown", "none", "book", "title",
    "document", "file", "final", "new", "scan", "scan1", "scan2", "scan3",
    "oebps", "content", "index", "cover", "default", "temp", "copy",
    "ebook", "epub", "pdf", "adobe", "indesign", "converted", "converted2",
}
EXT = re.compile(r"\.(x?html?|epub|mobi|azw3?|jpg|png|pdf|opf|ncx)$", re.I)
OL = "https://openlibrary.org"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def real(k):
    return LIB / k.split("::", 1)[-1]


def authors(a):
    try:
        return [n for n in (GA.parse_authors(a or "") or []) if norm(n)]
    except Exception:
        return []


def bad_title(t, an):
    t = (t or "").strip()
    if not t:
        return True, "empty"
    if EXT.search(t) or "/" in t or "\\" in t:
        return True, "filename or path"
    low = t.lower().strip()
    if low in JUNK:
        return True, f"generic {low!r}"
    n = norm(t)
    if an and n in an:
        return True, "is the author"
    if len(t) > 160 or len(n.split()) > 14:
        return True, "implausibly long"
    c = {}
    for w in n.split():
        c[w] = c.get(w, 0) + 1
    if c and max(c.values()) >= 3:
        return True, "repeated-word noise"
    return False, "ok"


def opf_fields(p):
    """Every dc:* in the OPF, plus an ISBN if present."""
    out = {}
    if p.suffix.lower() != ".epub":
        return out
    try:
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.lower().endswith(".opf"):
                    t = z.read(n).decode("utf-8", "replace")
                    for tag in ("title", "creator", "publisher", "date",
                                "description", "subject", "rights",
                                "identifier", "language", "source"):
                        vals = re.findall(
                            rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", t, re.S | re.I)
                        if vals:
                            out[tag] = [re.sub(r"<[^>]+>", " ", v).strip()
                                        for v in vals][:3]
                    return out
    except Exception:
        return {}
    return {}


def doc_title(p):
    """<title> or the first <h1> in the first spine document."""
    if p.suffix.lower() != ".epub":
        return ""
    try:
        with zipfile.ZipFile(p) as z:
            docs = [n for n in z.namelist()
                    if n.lower().endswith((".xhtml", ".html", ".htm"))]
            for n in docs[:3]:
                t = z.read(n).decode("utf-8", "replace")
                m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
                if m and norm(m.group(1)):
                    return norm(m.group(1))
                m = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S | re.I)
                if m:
                    txt = norm(re.sub(r"<[^>]+>", " ", m.group(1)))
                    if txt:
                        return txt
    except Exception:
        pass
    return ""


def isbn_of(fields):
    for v in fields.get("identifier", []):
        if v.lower().startswith("urn:isbn"):
            d = v.split(":")[-1].strip().replace("-", "")
            if len(d) in (10, 13):
                return d
    return ""


def openlibrary_title(isbn):
    """The strongest external evidence available: Goodreads is behind a WAF
    challenge from this host, but OpenLibrary answers."""
    if not isbn:
        return "", ""
    try:
        url = f"{OL}/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data"
        req = urllib.request.Request(url, headers={"User-Agent": "GoodBooks/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        entry = d.get(f"ISBN:{isbn}") or {}
        if not entry:
            return "", ""
        title = entry.get("title") or ""
        names = [a.get("name") for a in (entry.get("authors") or []) if a.get("name")]
        return title, " & ".join(names)
    except Exception:
        return "", ""


def fname_title(p):
    return p.stem.rsplit("-", 1)[0] if "-" in p.stem else p.stem


candidates = []
for k, v in META.items():
    if not isinstance(v, dict):
        continue
    p = real(k)
    if not p.exists():
        continue
    stored = (v.get("title") or "").strip()
    if not stored:
        continue
    an = authors(v.get("author"))
    is_bad, why = bad_title(stored, an)
    if not is_bad:
        continue
    candidates.append((k, p, stored, an, why))

print("=" * 76)
print(f"RECORDS WITH A JUNK STORED TITLE: {len(candidates)}")
print("=" * 76)
from collections import Counter  # noqa: E402
print("  by reason:", dict(Counter(w for *_, w in candidates).most_common()))

resolved = {"dc": [], "doc": [], "isbn": [], "fname": [], "none": []}

for k, p, stored, an, why in candidates:
    fields = opf_fields(p)
    dct = norm(fields.get("title", [""])[0]) if fields.get("title") else ""
    d_ok = dct and not bad_title(dct, an)[0]

    dt = doc_title(p)
    dt_ok = dt and not bad_title(dt, an)[0]

    isbn = isbn_of(fields)
    ol_t, ol_a = openlibrary_title(isbn) if isbn else ("", "")
    ol_ok = ol_t and not bad_title(ol_t, an + authors(ol_a))[0]

    if d_ok:
        resolved["dc"].append((k, p, stored, fields["title"][0], "dc:title"))
    elif ol_ok:
        resolved["isbn"].append((k, p, stored, ol_t, f"OpenLibrary ISBN {isbn}"))
    elif dt_ok:
        resolved["doc"].append((k, p, stored, dt, "<title>/<h1>"))
    elif bad_title(fname_title(p), an)[0]:
        resolved["none"].append((k, p, stored, "", ""))
    else:
        resolved["fname"].append((k, p, stored, fname_title(p), "filename"))

isbn_hits = sum(1 for k, p, stored, an, why in candidates
                if isbn_of(opf_fields(p)))
print()
print(f"  records carrying a usable ISBN in the OPF : {isbn_hits}")
print("  (OpenLibrary can only arbitrate when there IS an ISBN;")
print("   Goodreads is unreachable from this host behind a WAF challenge)")
print()
print("=== what new evidence exists ===")
for src_name, label in (("isbn", "OpenLibrary by ISBN  (external, strongest)"),
                        ("dc", "a usable dc:title in the OPF"),
                        ("doc", "<title>/<h1> in the document"),
                        ("fname", "the filename only"),
                        ("none", "NOTHING usable -- needs a human")):
    rows = resolved[src_name]
    print(f"  {len(rows):>4}  {label}")

print()
for src_name in ("isbn", "dc", "doc", "fname"):
    rows = resolved[src_name]
    if not rows:
        continue
    print("=" * 76)
    print(f"FROM {src_name.upper()}")
    print("=" * 76)
    for k, p, stored, new, why in rows[:12]:
        print(f"  {p.name[:58]}")
        print(f"    stored : {stored[:58]!r}")
        print(f"    NEW    : {new[:58]!r}   [{why}]")
    if len(rows) > 12:
        print(f"    ... {len(rows)-12} more")
    print()

print("=" * 76)
print(f"STILL NO EVIDENCE: {len(resolved['none'])}")
print("=" * 76)
for k, p, stored, _, _ in resolved["none"][:10]:
    print(f"  {p.name[:64]}")
    print(f"    stored: {stored[:64]!r}")
