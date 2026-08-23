#!/usr/bin/env bash
set -u

# Validate the first 30 tasks by default.
# Task indexes are zero-based, so 0..29 means task 01..30.
#
# Usage:
#   bash scripts/pipeline/stage2/validate_all.sh
#   TASK_START=0 TASK_END=29 SEEDS="1 2 3" MAX_JOBS=4 \
#     bash scripts/pipeline/stage2/validate_all.sh
#
# Logs are written to LOG_DIR and preserved after the script exits.

PYTHON="${PYTHON:-python}"
TASK_START="${TASK_START:-0}"
TASK_END="${TASK_END:-29}"
MAX_JOBS="${MAX_JOBS:-3}"
SEEDS="${SEEDS:-1 2 3}"
# 1 = PASS requires score==1.0 and config_exact_match==true (default).
# 0 = PASS requires only score==1.0 and ignores config_exact_match.
REQUIRE_CONFIG_MATCH="${REQUIRE_CONFIG_MATCH:-1}"
LOG_DIR="${LOG_DIR:-logs/task_validation_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$LOG_DIR"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python executable not found: $PYTHON" >&2
    exit 2
fi

if (( TASK_START < 0 || TASK_END < TASK_START )); then
    echo "Invalid task range: TASK_START=$TASK_START TASK_END=$TASK_END" >&2
    exit 2
fi

TMPDIR_RESULTS=$(mktemp -d)
LOCKFILE=$(mktemp)
trap 'rm -rf "$TMPDIR_RESULTS" "$LOCKFILE"' EXIT

log() {
    (flock 9; echo "$1") 9>"$LOCKFILE"
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

run_task() {
    local task_index=$1
    local pass=0
    local fail=0
    local failed_seeds=()
    local task_path
    task_path=$(task_name "$task_index")

    log "===== task_index $task_index: $task_path ====="

    for seed in $SEEDS; do
        local log_file="$LOG_DIR/task_${task_index}_seed_${seed}.log"
        local status=0
        "$PYTHON" -m scripts.pipeline.stage2.validate_task \
            --task_index "$task_index" --seed "$seed" >"$log_file" 2>&1
        status=$?

        local score
        score=$(grep -oP '(?<=score: )\S+' "$log_file" | tail -n 1 || true)
        local config_match
        config_match=$(grep -oP '(?<=config_exact_match: )\S+' "$log_file" | tail -n 1 || true)

        local ok=0
        if [[ "$status" -eq 0 && "$score" == "1.0" ]]; then
            if [[ "$REQUIRE_CONFIG_MATCH" != "1" || "$config_match" == "true" ]]; then
                ok=1
            fi
        fi

        if [[ "$ok" -eq 1 ]]; then
            pass=$((pass + 1))
            log "[task $task_index | seed $seed] PASS"
        else
            fail=$((fail + 1))
            failed_seeds+=("seed=$seed exit=$status score=${score:-N/A} config_match=${config_match:-N/A} log=$log_file")
            log "[task $task_index | seed $seed] FAIL (exit=$status score=${score:-N/A} config_match=${config_match:-N/A}) log=$log_file"
        fi
    done

    {
        echo "$pass $fail"
        printf '%s\n' "${failed_seeds[@]}"
    } > "$TMPDIR_RESULTS/task_$task_index"
}

export -f log task_name run_task
export PYTHON SEEDS LOG_DIR TMPDIR_RESULTS LOCKFILE REQUIRE_CONFIG_MATCH

task_total=$((TASK_END - TASK_START + 1))
seed_count=$(wc -w <<< "$SEEDS" | tr -d ' ')

echo "Starting validation: task indexes $TASK_START..$TASK_END ($task_total tasks), $seed_count seed(s), $MAX_JOBS worker(s)"
echo "Seeds: $SEEDS"
echo "Logs: $LOG_DIR"
echo ""

running=0
for task_index in $(seq "$TASK_START" "$TASK_END"); do
    run_task "$task_index" &
    running=$((running + 1))

    if (( running >= MAX_JOBS )); then
        wait -n
        running=$((running - 1))
    fi
done

wait

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="

total_pass=0
total_fail=0

for task_index in $(seq "$TASK_START" "$TASK_END"); do
    result_file="$TMPDIR_RESULTS/task_$task_index"
    if [[ ! -f "$result_file" ]]; then
        echo "  Task $task_index: no result file [FAIL]"
        total_fail=$((total_fail + seed_count))
        continue
    fi

    read -r pass fail < "$result_file"
    total_pass=$((total_pass + pass))
    total_fail=$((total_fail + fail))

    status="OK"
    [[ "$fail" -gt 0 ]] && status="FAIL"
    echo "  Task $task_index: $pass/$seed_count passed [$status]"

    tail -n +2 "$result_file" | while read -r line; do
        [[ -n "$line" ]] && echo "    -> $line"
    done
done

echo "------------------------------------------"
echo "  Total: $total_pass passed, $total_fail failed (out of $((task_total * seed_count)))"
echo "  Logs: $LOG_DIR"

if (( total_fail > 0 )); then
    exit 1
fi
