#!/usr/bin/env python3
"""Measure the REAL words-per-second-of-playback from finished audiobooks.

For every done job: words in its chapters vs the duration of the result file.
Then compare against WORDS_PER_SECOND_PLAYBACK = 84.0.
"""
import json, subprocess
from pathlib import Path

JOBS = Path('/usr/local/bin/GoodBooks/data/audiobook_jobs')
import gb_audiobook as AB

print('code constant WORDS_PER_SECOND_PLAYBACK =', AB.WORDS_PER_SECOND_PLAYBACK)
print()
print(f"{'book':42} {'words':>8} {'ch':>3} {'audio_s':>9} {'w/s':>7} {'file_s':>9} {'ff_s':>7}")
rows = []
for jf in sorted(JOBS.glob('*.json')):
    try:
        j = json.loads(jf.read_text())
    except Exception:
        continue
    if j.get('phase') != 'done':
        print(f"{(j.get('title') or jf.name)[:42]:42} phase={j.get('phase')}")
        continue
    words = 0
    for ch in (j.get('chapters') or []):
        words += int(ch.get('words') or 0)
    res = j.get('result') or j.get('result_path') or ''
    file_s = 0.0
    if res and Path(res).exists():
        try:
            r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'csv=p=0', res], capture_output=True, text=True, timeout=60)
            file_s = float((r.stdout or '0').strip() or 0)
        except Exception:
            pass
    audio_s = float(j.get('audio_seconds') or 0.0)
    wps = (words / audio_s) if audio_s else 0
    wps_file = (words / file_s) if file_s else 0
    rows.append((words, audio_s, file_s, wps, wps_file, j.get('title') or jf.name,
                 len(j.get('chapters') or [])))
    print(f"{(j.get('title') or jf.name)[:42]:42} {words:8} {len(j.get('chapters') or []):3} "
          f"{audio_s:9.1f} {wps:7.1f} {file_s:9.1f} {wps_file:7.1f}")

print()
if rows:
    tot_w = sum(r[0] for r in rows)
    tot_a = sum(r[1] for r in rows)
    tot_f = sum(r[2] for r in rows)
    print('COMBINED words:', tot_w)
    print('audio_seconds :', round(tot_a, 1), ' -> w/s =', round(tot_w / tot_a, 2) if tot_a else 'n/a')
    print('ffprobe file  :', round(tot_f, 1), ' -> w/s =', round(tot_w / tot_f, 2) if tot_f else 'n/a')
    # what the UI would claim vs reality for a typical book
    print()
    print('For a 201,709-word book the UI currently claims:',
          round(201709 / 84.0 / 60.0, 1), 'min playback')
    if tot_f:
        print('Reality at the measured rate              :',
              round(201709 / (tot_w / tot_f) / 60.0, 1), 'min playback')
    # speaking rate sanity: 150 wpm narration = 2.5 w/s
    if tot_f:
        wps = tot_w / tot_f
        print(f'\nmeasured {wps:.1f} words/s of audio = {wps*60:.0f} words/minute')