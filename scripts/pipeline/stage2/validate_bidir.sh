#!/usr/bin/env bash
set -u

# Bidirectional validator: two workers claim tasks from opposite ends.
#
# Bidirectional runner behavior:
#   - An flock-protected claim file prevents workers from taking the same task.
#     Duplicate indexes are unsafe because TaskEnvironment recreates projects
#     using task_data["scenario"] as the project name.
#   - Dynamic claiming balances uneven task durations.
#   - By default PASS checks score only, not config_exact_match.
#
# Usage:
#   TASK_START=47 TASK_END=95 bash scripts/pipeline/stage2/validate_bidir.sh
#   PYTHON=/path/to/python SEEDS="1" bash scripts/pipeline/stage2/validate_bidir.sh
#
# Logs remain in LOG_DIR after exit.

PYTHON="${PYTHON:-python}"
TASK_START="${TASK_START:-47}"
TASK_END="${TASK_END:-95}"
SEEDS="${SEEDS:-1}"
# 1 = PASS requires score==1.0 and config_exact_match==true.
# 0 = PASS requires only score==1.0 (default).
REQUIRE_CONFIG_MATCH="${REQUIRE_CONFIG_MATCH:-0}"
LOG_DIR="${LOG_DIR:-logs/task_validation_bidir_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$LOG_DIR"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python executable not found: $PYTHON" >&2
    exit 2
fi

if (( TASK_START < 0 || TASK_END < TASK_START )); then
    echo "Invalid task range: TASK_START=$TASK_START TASK_END=$TASK_END" >&2
    exit 2
fi

RESULTS_DIR="$LOG_DIR/results"
mkdir -p "$RESULTS_DIR"

# Claim state uses next_lo/next_hi cursors serialized by CLAIM_LOCK.
CLAIM_DIR=$(mktemp -d)
CLAIM_STATE="$CLAIM_DIR/cursors"
CLAIM_LOCK="$CLAIM_DIR/claim.lock"
PRINT_LOCK="$CLAIM_DIR/print.lock"
printf '%s %s\n' "$TASK_START" "$TASK_END" > "$CLAIM_STATE"
: > "$CLAIM_LOCK"
: > "$PRINT_LOCK"
trap 'rm -rf "$CLAIM_DIR"' EXIT

log() {
    flock 9 || true
    printf '[%s] %s\n' "$(date +%H:%M:%S)" "$1"
    exec 9>&-
} 9>>"$PRINT_LOCK"

# claim <direction> prints the claimed task index, or nothing if exhausted.
# fwd claims from the low end; rev claims from the high end.
claim() {
    local direction=$1
    (
        flock 9
        read -r lo hi < "$CLAIM_STATE"
        if (( lo > hi )); then
            echo ""
        elif [[ "$direction" == "fwd" ]]; then
            echo "$lo"
            printf '%s %s\n' "$((lo + 1))" "$hi" > "$CLAIM_STATE"
        else
            echo "$hi"
            printf '%s %s\n' "$lo" "$((hi - 1))" > "$CLAIM_STATE"
        fi
    ) 9>"$CLAIM_LOCK"
}

task_name() {
    "$PYTHON" - "$1" <<'PY'
import json
import sys

idx = int(sys.argv[1])
with open("task/all_tasks.json", "r") as f:
    tasks = json.load(f)
print(tasks[idx] if 0 <= idx < len(tasks) else f"<missing task index {idx}>")
PY
}

run_one_task() {
    local task_index=$1
    local worker=$2
    local task_path
    task_path=$(task_name "$task_index")
    local pass=0 fail=0
    local details=()

    log "W$worker >>> task_index $task_index: $task_path"

    for seed in $SEEDS; do
        local log_file="$LOG_DIR/task_${task_index}_seed_${seed}.log"
        local status=0
        "$PYTHON" -m scripts.pipeline.stage2.validate_task \
            --task_index "$task_index" --seed "$seed" >"$log_file" 2>&1
        status=$?

        local score config_match
        score=$(grep -oP '(?<=score: )\S+' "$log_file" | tail -n 1 || true)
        config_match=$(grep -oP '(?<=config_exact_match: )\S+' "$log_file" | tail -n 1 || true)

        # Report failed check IDs for direct diagnosis.
        local failed_ids
        failed_ids=$("$PYTHON" - "$log_file" <<'PY' || true
import json, re, sys
try:
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
except OSError:
    sys.exit(0)
m = re.search(r'^testcases_passed:\n(\{.*?\n\})\n', text, re.S | re.M)
if not m:
    sys.exit(0)
try:
    data = json.loads(m.group(1))
except Exception:
    sys.exit(0)
bad = [k for k, v in data.items() if not v]
if bad:
    print(",".join(bad))
PY
)

        local ok=0
        if [[ "$status" -eq 0 && "$score" == "1.0" ]]; then
            if [[ "$REQUIRE_CONFIG_MATCH" != "1" || "$config_match" == "true" ]]; then
                ok=1
            fi
        fi

        if (( ok == 1 )); then
            pass=$((pass + 1))
            log "W$worker     task $task_index seed $seed PASS (config_match=${config_match:-N/A})"
        else
            fail=$((fail + 1))
            details+=("seed=$seed exit=$status score=${score:-N/A} config_match=${config_match:-N/A} failed_ids=${failed_ids:-none} log=$log_file")
            log "W$worker     task $task_index seed $seed FAIL exit=$status score=${score:-N/A} failed_ids=${failed_ids:-none}"
        fi
    done

    {
        echo "$task_index|$pass|$fail|$task_path"
        printf '%s\n' "${details[@]+"${details[@]}"}"
    } > "$RESULTS_DIR/task_$task_index"
}

worker_loop() {
    local direction=$1
    local worker=$2
    while true; do
        local idx
        idx=$(claim "$direction")
        [[ -z "$idx" ]] && break
        run_one_task "$idx" "$worker"
    done
    log "W$worker ($direction) done, no tasks left."
}

seed_count=$(wc -w <<< "$SEEDS" | tr -d ' ')
task_total=$((TASK_END - TASK_START + 1))

echo "=========================================="
echo "Bidirectional validation"
echo "  task indexes : $TASK_START..$TASK_END ($task_total tasks)"
echo "  seeds        : $SEEDS ($seed_count per task)"
echo "  workers      : 2 (W1 forward from $TASK_START, W2 reverse from $TASK_END)"
echo "  PASS rule    : score==1.0$([[ "$REQUIRE_CONFIG_MATCH" == "1" ]] && echo " AND config_exact_match" || echo " (config_exact_match ignored)")"
echo "  logs         : $LOG_DIR"
echo "=========================================="
echo ""

worker_loop fwd 1 &
W1=$!
# Stagger startup so both workers do not request GNS3 ports simultaneously.
sleep 20
worker_loop rev 2 &
W2=$!

wait "$W1" "$W2"

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="

total_pass=0
total_fail=0
failed_tasks=()

for task_index in $(seq "$TASK_START" "$TASK_END"); do
    result_file="$RESULTS_DIR/task_$task_index"
    if [[ ! -f "$result_file" ]]; then
        echo "  task $task_index: NO RESULT [FAIL]"
        total_fail=$((total_fail + seed_count))
        failed_tasks+=("$task_index")
        continue
    fi

    IFS='|' read -r idx pass fail tpath < <(head -n 1 "$result_file")
    total_pass=$((total_pass + pass))
    total_fail=$((total_fail + fail))

    if (( fail > 0 )); then
        echo "  task $task_index: $pass/$seed_count [FAIL] $tpath"
        failed_tasks+=("$task_index")
        tail -n +2 "$result_file" | while read -r line; do
            [[ -n "$line" ]] && echo "      -> $line"
        done
    else
        echo "  task $task_index: $pass/$seed_count [OK]   $tpath"
    fi
done

echo "------------------------------------------"
echo "  Total: $total_pass passed, $total_fail failed (out of $((task_total * seed_count)))"
if (( ${#failed_tasks[@]} > 0 )); then
    echo "  Failed task indexes: ${failed_tasks[*]}"
fi
echo "  Logs: $LOG_DIR"

if (( total_fail > 0 )); then
    exit 1
fi
