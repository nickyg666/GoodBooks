#!/usr/bin/env python3
"""Purge covers that belong to a different book, read from the rendered scan.

Input: /tmp/gb_allcards.json (title + cover per rendered card, 3,120 of 4,786).
A cover shared by two DIFFERENT titles is a wrong adoption. A cover shared by
two records with the SAME title is just one book in two formats and is left
alone.

Only the cover is removed. Titles, descriptions, ratings and genres are not
touched -- "don't delete anything" -- and the record is stamped so it can be
re-enriched (and now pass the match gate) instead of being silently wrong.
"""
import json, datetime, os, shutil, sys
from pathlib import Path

ROOT = Path('/usr/local/bin/GoodBooks')
META = ROOT / 'data/library_metadata.json'
CARDS = Path('/tmp/gb_allcards.json')

cards = json.loads(CARDS.read_text())
meta = json.loads(META.read_text())
print('cards:', len(cards), ' records:', len(meta))

def norm(t):
    return ' '.join(''.join(c.lower() if c.isalnum() or c.isspace() else ' '
                            for c in (t or '')).split())

# which covers are shared across different titles?
from collections import defaultdict
bycover = defaultdict(list)
for c in cards:
    if c['img']:
        bycover[c['img']].append(norm(c['t']))

wrong_imgs = {}
for img, titles in bycover.items():
    if len(titles) > 1 and len(set(titles)) > 1:
        wrong_imgs[img] = sorted(set(titles))
print('covers shared across DIFFERENT titles:', len(wrong_imgs))

# map back to records. Card titles are display titles; records are keyed by path.
# Match on the cover value itself, which is exact.
victim_keys = {}
for k, v in meta.items():
    cov = str(v.get('cover') or '')
    if cov and any(cov == img for img in wrong_imgs):
        victim_keys[k] = cov

print('records holding a wrong cover:', len(victim_keys))
for k, cov in victim_keys.items():
    t = str(meta[k].get('title') or '')
    print(f'   {t[:70]!r}')

if not victim_keys:
    print('\nnothing to purge -- the wrong covers are not in the metadata file')
    sys.exit(0)

# backup, then remove ONLY the cover field
ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
bak = META.with_name(f'library_metadata.json.pre-coverpurge-{ts}')
shutil.copy2(META, bak)
print('\nbackup:', bak.name)

changed = 0
for k in victim_keys:
    m = meta[k]
    removed = m.pop('cover', None)
    m['cover_purged'] = {
        'when': ts, 'why': 'cover was shared with a different book',
        'url': removed,
    }
    # the enrichment failure tombstone would otherwise block a correct retry
    m['enrichment_note'] = 'cover_purged'
    changed += 1

tmp = META.with_name(f'library_metadata.json.tmp.purge.{os.getpid()}.{ts}')
tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
with open(tmp, 'rb') as f:
    os.fsync(f.fileno())
os.replace(tmp, META)
dfd = os.open(str(META.parent), os.O_RDONLY)
os.fsync(dfd)
os.close(dfd)

# invariants
after = json.loads(META.read_text())
assert len(after) == len(meta), 'record count changed!'
assert sum(1 for v in after.values() if v.get('title')) == \
       sum(1 for v in json.loads(bak.read_text()).values() if v.get('title')), \
       'title count changed!'
print(f'\npurged {changed} covers; records {len(after)} (unchanged), titles intact')
print('remaining covers:', sum(1 for v in after.values() if v.get('cover')))