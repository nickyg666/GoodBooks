GoodBooks: why nothing downloaded or sent, and the real fix

The chain was broken in four places, each found by measurement, and the
symptoms all looked like "the network is flaky".

1. User-Agent. The download client sent requests' DEFAULT User-Agent.
   LibGen's nginx answers a non-browser agent with a 638-byte
   "Welcome to nginx!" decoy, which the HTML sniffer correctly rejected. The
   same URL with a browser UA returns a real page from the same IP. The
   search client had a UA; the download client never did.

2. LibGen needs a keyed second hop. get.php?md5=X returns an HTML page, not
   an error. That page contains one download link:
   get.php?md5=X&key=<16 alnum>. Only that keyed request returns
   application/octet-stream. The old code made request one, called the HTML a
   failure, and moved on. New module: gb_keyed.py.

3. AA and LibGen use different md5 namespaces. An AA md5 returns "File not
   found in DB" on every LibGen mirror, which says nothing about whether
   LibGen holds the book. The title-query ladder is the only way to resolve
   those, and the branch that runs when AA is down never called it. Also
   added the ladder, because LibGen indexes the BOOK, not the Goodreads
   metadata blob: "The Butcher's Masquerade: Dungeon Crawler Carl (Book 5)"
   returns zero hits, the shortened form does not.

4. AA was tried first and is 100% broken. All 213 slow_download attempts
   returned synthetic HTML. Because AA is first, every book burned minutes
   in the stealth browser before reaching the LibGen fallback that works.
   Per Nick's call: a per-entry budget of 2 distinct AA mirror failures, then
   fall back to LibGen. Shared across the primary, waitlist and external
   mirror loops, which were all unbounded (1744 attempts in one run).

Also: sent_ledger.json is vestigial. Nothing in app.py writes it. The real
record of a send is kindle_sent / kindle_sent_email / kindle_sent_timestamp in
data/history.json. Do not use the ledger as evidence of delivery.

Verified end to end on a live feed run: Mean Spirited.epub, 6,712,779 bytes,
valid EPUB, downloaded by the ladder, queued for batch send, SMTP accepted,
and recorded kindle_sent=True at 2026-09-30T21:27:31Z for
sagegelinas_mini@kindle.com. Earlier, Dear Debbie (1,869,525 bytes) and The
Girl Who Steals Christmas (1,740,096 bytes) were pulled through the same
keyed path, each verified by reading title/creator from content.opf.
