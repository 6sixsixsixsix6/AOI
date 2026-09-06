#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

# Stub both external commands so this test never starts Docker or calls an API.
printf 'exit 0\n' > "$TEST_ROOT/runner.sh"
touch "$TEST_ROOT/generator.py"
sed 's/\r$//' "$PROJECT_ROOT/scripts/run_batch_experiments.sh" > "$TEST_ROOT/batch.sh"
python3() {
    printf '%s\n' "$@" > "$AOI_PROJECT/generator-args.txt"
    return 37
}
export -f python3

check_case() (
    local case_name="$1"
    local expected="$2"
    local caller_value="${3:-}"
    local key rc=0
    for key in ${!AOI_@}; do unset "$key"; done
    export AOI_PROJECT="$TEST_ROOT"
    export AOI_GENERATOR="$TEST_ROOT/generator.py"
    export AOI_RUNNER="$TEST_ROOT/runner.sh"
    export AOI_EXPERIMENT_LOCK_DIR="$TEST_ROOT/experiment.lock"
    export PENTESTGPT_ENV_FILE="$TEST_ROOT/missing.env"
    printf 'AOI_SCENARIO_EXCLUDE=fake_flag,fake_cve\n' > "$TEST_ROOT/.env"
    if [[ "$case_name" != dotenv ]]; then
        export AOI_SCENARIO_EXCLUDE="$caller_value"
    fi
    bash "$TEST_ROOT/batch.sh" multiple 1 > "$TEST_ROOT/$case_name.log" 2>&1 || rc=$?
    if [[ "$rc" -ne 37 ]]; then
        cat "$TEST_ROOT/$case_name.log"
        echo "Unexpected exit status: $rc" >&2
        exit 1
    fi
    local -a args=()
    mapfile -t args < "$TEST_ROOT/generator-args.txt"
    local index found=0
    for ((index=0; index<${#args[@]}; index++)); do
        if [[ "${args[index]}" == --exclude ]]; then
            [[ "${args[index+1]}" == "$expected" ]]
            found=$((found + 1))
        fi
    done
    [[ "$found" -eq 1 ]]
    [[ ! -e "$AOI_EXPERIMENT_LOCK_DIR" ]]
    printf 'PASS: %s\n' "$case_name"
)

check_case dotenv fake_flag,fake_cve
check_case override fake_secret fake_secret
check_case empty "" ""
