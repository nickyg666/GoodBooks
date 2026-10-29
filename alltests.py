import sys
sys.path.insert(0, '.')
import gb_covermatch

for fname in ['0451524934.jpg', '0743273567.jpg', '0261102214.jpg']:
    path = '/tmp/' + fname
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except Exception as e:
        print(fname, 'file error', e)
        continue
    res = gb_covermatch.identify_cover(data)
    print(fname)
    print('  cropped:', res['cropped'])
    print('  isbn_guess:', res['isbn_guess'])
    print('  queries:', res['queries'][:2])
    print('  yandex_hits:', len(res['yandex_hits']))
    print('  ranked:', len(res['ranked']))
    if res['ranked']:
        print('  top:', res['ranked'][0]['url'][:70], 'dist', res['ranked'][0]['distance'])
    print('  local_matches:', res['local_matches'][:3])