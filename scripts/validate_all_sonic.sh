#!/bin/bash
export NETCONF_PLATFORM=sonic
mkdir -p results /tmp/vlogs
OUT=results/sonic_validate.txt
touch "$OUT"

# Lock file to prevent double-runs
exec 9>"/tmp/validate_all_sonic.lock"
flock -n 9 || { echo "ERROR: Another instance is already running! Exiting."; exit 1; }

for d in sonic-gen/*/; do
  n=$(basename "$d")
  # Skip manual/special tasks
  case "$n" in 19.*|32.*|33.*|39.*|46.*) continue;; esac
  
  # Skip if already scored successfully (look for "gt=")
  if grep -q "^$n | startup=" "$OUT"; then
    continue
  fi
  # Skip if already marked as failed (prevents infinite crash loops)
  if grep -q "^$n | NO_SCORE" "$OUT" || grep -q "^$n | APPLY_FAIL" "$OUT"; then
    continue
  fi

  f="${d}${n}.json"
  log="/tmp/vlogs/${n}.txt"
  
  echo "=== Testing $n ==="
  timeout 5400 python -m scripts.sonic_validate "$f" 1 > "$log" 2>&1
  
  scen=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["scenario"])' "$f" 2>/dev/null)
  python3 scripts/gns3_cleanup.py "SONiC-$scen" >> "$log" 2>&1
  sleep 30
  
  # Use grep -a to treat binary files as text (fixes the crash bug)
  gt_score=$(grep -a -m1 '^groundtruth_score:' "$log" | awk '{print $2}')
  su_score=$(grep -a -m1 '^startup_score:' "$log" | awk '{print $2}')
  
  if [ -n "$gt_score" ]; then
    failed=$(grep -a -oE '"[0-9]+(-[0-9]+)?": false' "$log" | cut -d'"' -f2 | tr '\n' ' ')
    echo "$n | startup=$su_score | gt=$gt_score | failed: ${failed:-none}" >> "$OUT"
  elif grep -a -q "No space left" "$log"; then
    echo "$n | DISK_FULL - loop stopped" >> "$OUT"; break
  elif grep -a -q "did not apply cleanly" "$log"; then
    echo "$n | APPLY_FAIL (groundtruth rejected by FRR)" >> "$OUT"
  else
    echo "$n | NO_SCORE (crash/timeout, see $log)" >> "$OUT"
  fi
done
echo "DONE" >> "$OUT"
