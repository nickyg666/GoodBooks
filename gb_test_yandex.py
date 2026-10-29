import gb_yandex2, sys
for p in ('/tmp/1984.jpg','/tmp/greatgatsby.jpg','/tmp/hobbit.jpg'):
    hits = gb_yandex2.reverse_search(open(p,'rb').read())
    print(p, 'hits:', len(hits))
    for h in hits[:5]:
        print('   ', repr(h['title'][:30]), h['url'][:80])