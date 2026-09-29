#!/bin/bash
# Drive the parallel metadata repair to completion, launching successive
# passes until the mangled count stops falling or the passes are exhausted.
cd /usr/local/bin/GoodBooks || exit 1
SHARDS=8
COUNT=170
MAXPASS=${1:-8}

echo "service: $(systemctl is-active GoodBooks.service)"
prev=-1
for pass in $(seq 1 $MAXPASS); do
  START=$(( (pass - 1) * COUNT ))
  echo "=== pass $pass (start=$START) ==="
  bash gb_ol_launch.sh "$SHARDS" "$COUNT" "$START" >/dev/null
  # wait for every shard to finish
  while pgrep -f gb_ol_par >/dev/null; do sleep 10; done
  cur=$(python3 -c "
import json, gb_openlib as ol
m = json.load(open('data/library_metadata.json'))
print(sum(1 for v in m.values() if isinstance(v, dict)
          and (v.get('title') or '').strip()
          and not ol.looks_clean_title(v['title'])))
")
  echo "pass $pass done; mangled remaining: $cur (was $prev)"
  if [ "$cur" -eq "$prev" ]; then
    echo "no further progress; stopping"
    break
  fi
  prev=$cur
done
echo "SWEEP COMPLETE"
