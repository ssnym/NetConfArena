#!/bin/bash
# NetConfArena — batch evaluation driver.
#
# Runs one agent configuration over a range of task templates and a number of
# trials, writing one result JSON per (task, model, mode, trial) under results/.
#
# Configuration is taken from the environment (see .env.example); the defaults
# below assume a GNS3 server on the local machine.
set -e

: "${GNS3_SERVER:=127.0.0.1}"
: "${GNS3_PROJECT_PATH:=$HOME/GNS3/projects}"
export GNS3_SERVER GNS3_PROJECT_PATH

# Agent / model configuration.
#   MODE       : ReAct (closed-loop) or OneShot (single-shot generation)
#   LLM_MODELS : space-separated model identifiers passed to --llm_model
#   NO_THINKING: "true" disables the model's reasoning channel where supported
#   TRIALS     : number of independent trials per task instance
#   SEEDS      : space-separated task-instantiation seeds (one instance per seed)
: "${MODE:=ReAct}"
: "${LLM_MODELS:=gpt-4o-mini}"
: "${NO_THINKING:=false}"
: "${TRIALS:=3}"
: "${SEEDS:=1}"

usage() {
    cat <<'EOF'
Usage: run.sh [START END]

  START, END   1-based task indices (inclusive). Defaults to 1 96.
               START may be greater than END to iterate in reverse.

Environment:
  MODE=ReAct|OneShot        agent harness (default ReAct)
  LLM_MODELS="a b"          models to evaluate (default gpt-4o-mini)
  NO_THINKING=true|false    disable reasoning channel (default false)
  TRIALS=N                  trials per task instance (default 3)
  SEEDS="1 2 3 4 5"         task-instantiation seeds (default 1)
  GNS3_SERVER=host          GNS3 server address (default 127.0.0.1)
  GNS3_PROJECT_PATH=path    GNS3 projects directory (default ~/GNS3/projects)

Examples:
  ./run.sh                                  # tasks 1..96, defaults
  ./run.sh 1 20                             # first 20 tasks
  SEEDS="1 2 3 4 5" TRIALS=8 ./run.sh 1 96  # 96 templates x 5 seeds x 8 trials
EOF
    exit 1
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
fi

if [[ -n "$1" && -n "$2" ]]; then
    if ! [[ "$1" =~ ^[0-9]+$ ]] || ! [[ "$2" =~ ^[0-9]+$ ]]; then
        echo "Error: START and END must be integers."
        usage
    fi
    TASK_START=$1
    TASK_END=$2
else
    TASK_START=1
    TASK_END=96
fi

if (( TASK_START <= TASK_END )); then
    STEP=1
else
    STEP=-1
fi

for seed in $SEEDS; do
    for trial in $(seq 1 "$TRIALS"); do
        for task_index in $(seq "$TASK_START" "$STEP" "$TASK_END"); do
            for llm_model in $LLM_MODELS; do
                python main.py \
                    --mode "$MODE" \
                    --llm_model "$llm_model" \
                    --task_index "$task_index" \
                    --seed "$seed" \
                    --no_thinking "$NO_THINKING" \
                    --trial "$trial"
            done
        done
    done
done
