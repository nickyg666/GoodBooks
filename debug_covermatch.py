import sys
sys.path.insert(0, '.')
import gb_covermatch
with open('/tmp/1984.jpg', 'rb') as f:
    data = f.read()
res = gb_covermatch.identify_cover(data)
print('cropped:', res['cropped'])
print('phash:', res['phash'])
print('yandex_hits count:', len(res['yandex_hits']))
if res['yandex_hits']:
    print('  first:', res['yandex_hits'][0][:80])
print('ranked count:', len(res['ranked']))
for i, r in enumerate(res['ranked']):
    print(f'  [{i}] url: {r["url"][:80]} distance: {r["distance"]}')
    if r.get('bytes'):
        # OCR on these bytes
        from gb_covermatch import ocr_queries
        qs = ocr_queries(r['bytes'])
        print(f'      OCR queries: {qs}')
    else:
        print(f'      no bytes')
print('ocr_queries (final):', res['ocr_queries'])
print('queries (final):', res['queries'])
print('local_matches:', res['local_matches'])
print('isbn_guess:', res['isbn_guess'])