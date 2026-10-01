"""Reconcile library_metadata.json against what is actually on disk.

The KeyError in rename_library_file_to_md5_format renamed a file and then
crashed before writing metadata, which permanently orphans a file. This
quantifies that: files on disk with no metadata record, and metadata records
with no file.
"""
import json
import os

ROOT = "/mnt/8tbdas/GoodBooks"
EXTS = (".epub", ".mobi", ".azw3", ".azw", ".pdf", ".cbz", ".rar", ".cbr",
        ".fb2", ".djvu", ".txt")

d = json.load(open("/usr/local/bin/GoodBooks/data/library_metadata.json"))
ids = set(d)

disk = set()
for base, _dirs, files in os.walk(ROOT):
    for f in files:
        if f.lower().endswith(EXTS):
            rel = os.path.relpath(os.path.join(base, f), ROOT)
            disk.add(os.path.realpath(ROOT) + "::" + rel)

print("metadata entries :", len(ids))
print("book files on disk:", len(disk))

orphans = disk - ids
phantoms = ids - disk
print()
print("ORPHANS  (file on disk, no metadata):", len(orphans))
for x in sorted(orphans)[:12]:
    print("    ", x.split("::")[-1][:88])
print()
print("PHANTOM metadata (record, no file) :", len(phantoms))
for x in sorted(phantoms)[:12]:
    print("    ", x.split("::")[-1][:88])
