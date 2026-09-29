#!/bin/bash
# Launch N parallel metadata-repair shards.
cd /usr/local/bin/GoodBooks || exit 1
SHARDS=${1:-8}
COUNT=${2:-170}
START=${3:-0}
systemctl is-active GoodBooks.service >/dev/null 2>&1 && \
  echo "WARNING: service is active; it will clobber writes" || true

for s in $(seq 0 $((SHARDS - 1))); do
  nohup python3 -u gb_ol_par.py \
      --shard "$s" --shards "$SHARDS" \
      --start "$START" --count "$COUNT" \
      --delay 0.05 --covers --apply \
      > "/tmp/par_$s.log" 2>&1 &
  echo "shard $s -> pid $!"
done
sleep 1
echo "running: $(pgrep -fc gb_ol_par)"
