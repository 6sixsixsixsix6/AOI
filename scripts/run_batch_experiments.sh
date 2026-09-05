#!/usr/bin/env bash
set -Eeuo pipefail

# Run one baseline control followed by one or more injected experiments.
# The existing repeatable runner owns container reset, injection, cleanup,
# token accounting, and per-run archives. This wrapper only queues runs.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="${AOI_PROJECT:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
GENERATOR="${AOI_GENERATOR:-$PROJECT/scripts/generate_scenario.py}"
RUNNER="${AOI_RUNNER:-$PROJECT/scripts/run_repeatable_experiment.sh}"
OUTPUT_ROOT="${AOI_SCENARIO_OUTPUT_ROOT:-$PROJECT/runs/XBEN-028-24/generated}"

MODE="${1:-}"
COUNT="${2:-}"
SELECTION="${AOI_SCENARIO_SELECTION:-qwen}"
GENERATION_MODE="${AOI_SCENARIO_MODE:-api}"
MAX_PROFILES="${AOI_SCENARIO_MAX:-3}"
SCENARIO_SELECT="${AOI_SCENARIO_SELECT:-}"
LOCK_DIR="${AOI_BATCH_LOCK_DIR:-${TMPDIR:-/tmp}/aoi-batch-experiment.lock}"

usage() {
    cat <<'USAGE'
用法:
  bash scripts/run_batch_experiments.sh repeat COUNT
  bash scripts/run_batch_experiments.sh multiple COUNT

模式:
  repeat    生成一个虚假环境，然后对同一个环境重复 COUNT 次注入攻击
  multiple  每轮先生成一个新的虚假环境，再进行一次注入攻击

默认生成配置:
  选择方式: AOI_SCENARIO_SELECTION=qwen
  生成方式: AOI_SCENARIO_MODE=api
  类型数量: AOI_SCENARIO_MAX=3

示例:
  bash scripts/run_batch_experiments.sh repeat 3
  bash scripts/run_batch_experiments.sh multiple 3
  AOI_SCENARIO_MODE=mock bash scripts/run_batch_experiments.sh multiple 3
  AOI_SCENARIO_SELECTION=manual \
  AOI_SCENARIO_SELECT=fake_framework,fake_cve \
  bash scripts/run_batch_experiments.sh repeat 3
USAGE
}

if [[ "$MODE" != "repeat" && "$MODE" != "multiple" ]]; then
    usage
    exit 2
fi
if [[ ! "$COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "COUNT 必须是大于 0 的整数: ${COUNT:-<空>}"
    usage
    exit 2
fi
if [[ ! "$MAX_PROFILES" =~ ^[1-9][0-9]*$ ]]; then
    echo "AOI_SCENARIO_MAX 必须是大于 0 的整数: $MAX_PROFILES"
    exit 2
fi
if [[ ! -f "$GENERATOR" ]]; then
    echo "找不到场景生成脚本: $GENERATOR"
    exit 3
fi
if [[ ! -f "$RUNNER" ]]; then
    echo "找不到攻击脚本: $RUNNER"
    exit 3
fi
if [[ "$SELECTION" == "manual" && -z "$SCENARIO_SELECT" ]]; then
    echo "手动选择时必须设置 AOI_SCENARIO_SELECT，例如 fake_framework,fake_cve"
    exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "需要 python3"
    exit 4
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "检测到已有批处理正在运行，锁目录: $LOCK_DIR"
    echo "确认没有其他实验后再删除该锁目录。"
    exit 5
fi
printf '%s\n' "pid=$$" "started=$(date --iso-8601=seconds 2>/dev/null || date)" > "$LOCK_DIR/owner"
release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap release_lock EXIT

cd "$PROJECT"

BATCH_ID="$(date +%Y%m%d_%H%M%S_%N)"
BATCH_DIR="$PROJECT/runs/XBEN-028-24/batches/batch-${MODE}-${BATCH_ID}"
mkdir -p "$BATCH_DIR"
BATCH_LOG="$BATCH_DIR/batch.log"
MANIFEST="$BATCH_DIR/manifest.tsv"

printf 'step\ttype\tscenario\trun_dir\tstatus\n' > "$MANIFEST"

echo "AOI 批处理目录: $BATCH_DIR" | tee -a "$BATCH_LOG"
echo "项目目录: $PROJECT" | tee -a "$BATCH_LOG"
echo "实验模式: $MODE" | tee -a "$BATCH_LOG"
echo "注入次数: $COUNT" | tee -a "$BATCH_LOG"
echo "Baseline: 仅执行 1 次" | tee -a "$BATCH_LOG"
echo "生成配置: selection=$SELECTION mode=$GENERATION_MODE max=$MAX_PROFILES" | tee -a "$BATCH_LOG"

step=0

generate_scenario() {
    local generation_label="$1"
    local generation_log="$BATCH_DIR/generate-${generation_label}.log"
    local rc=0
    local -a generator_args=(
        --selection "$SELECTION"
        --mode "$GENERATION_MODE"
        --max "$MAX_PROFILES"
        --output-root "$OUTPUT_ROOT"
    )
    if [[ -n "$SCENARIO_SELECT" ]]; then
        generator_args+=(--select "$SCENARIO_SELECT")
    fi

    echo | tee -a "$BATCH_LOG"
    echo "=== 生成虚假环境: $generation_label ===" | tee -a "$BATCH_LOG"
    set +e
    python3 "$GENERATOR" "${generator_args[@]}" \
        2>&1 | tee "$generation_log" | tee -a "$BATCH_LOG"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ "$rc" -ne 0 ]]; then
        echo "场景生成失败，停止批处理: $generation_label" | tee -a "$BATCH_LOG"
        return "$rc"
    fi

    SCENARIO_DIR="$(sed -n 's/^场景目录: //p' "$generation_log" | tail -n 1)"
    if [[ -z "$SCENARIO_DIR" || ! -d "$SCENARIO_DIR" || ! -f "$SCENARIO_DIR/scenario.json" || ! -f "$SCENARIO_DIR/fake_world.json" ]]; then
        echo "未能从生成输出中定位完整场景目录: ${SCENARIO_DIR:-<空>}" | tee -a "$BATCH_LOG"
        return 20
    fi
    SCENARIO_DIR="$(cd -- "$SCENARIO_DIR" && pwd)"
    echo "场景目录: $SCENARIO_DIR" | tee -a "$BATCH_LOG"
}

run_attack() {
    local attack_type="$1"
    local scenario_dir="${2:-}"
    local label="$3"
    local rc=0
    local run_dir=""

    step=$((step + 1))
    local output_log="$BATCH_DIR/step-${step}-${attack_type}.log"
    echo | tee -a "$BATCH_LOG"
    echo "=== 第 $step 步: $label ===" | tee -a "$BATCH_LOG"

    set +e
    if [[ "$attack_type" == "baseline" ]]; then
        bash "$RUNNER" baseline 2>&1 | tee "$output_log" | tee -a "$BATCH_LOG"
    else
        bash "$RUNNER" injected "$scenario_dir" 2>&1 | tee "$output_log" | tee -a "$BATCH_LOG"
    fi
    rc=${PIPESTATUS[0]}
    set -e

    run_dir="$(sed -n 's/^本轮记录: //p' "$output_log" | tail -n 1)"
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "$step" "$attack_type" "${scenario_dir:--}" "${run_dir:--}" "$rc" >> "$MANIFEST"

    if [[ "$rc" -ne 0 ]]; then
        echo "该步骤失败，已停止后续实验。请先检查恢复状态: ${run_dir:-未定位运行目录}" | tee -a "$BATCH_LOG"
        return "$rc"
    fi
    echo "该步骤完成，容器已通过脚本的恢复检查。" | tee -a "$BATCH_LOG"
}

echo "=== 第 1 步: Baseline 对照攻击（仅一次） ===" | tee -a "$BATCH_LOG"
run_attack baseline "" "Baseline 对照攻击"

if [[ "$MODE" == "repeat" ]]; then
    generate_scenario "shared"
    for index in $(seq 1 "$COUNT"); do
        run_attack injected "$SCENARIO_DIR" "同一场景第 ${index}/${COUNT} 次注入攻击"
    done
else
    for index in $(seq 1 "$COUNT"); do
        generate_scenario "case-${index}"
        run_attack injected "$SCENARIO_DIR" "新场景第 ${index}/${COUNT} 次注入攻击"
    done
fi

echo | tee -a "$BATCH_LOG"
echo "=== 批处理完成 ===" | tee -a "$BATCH_LOG"
echo "批处理日志: $BATCH_LOG" | tee -a "$BATCH_LOG"
echo "批处理清单: $MANIFEST" | tee -a "$BATCH_LOG"
echo "Baseline 归档和每个注入归档位于: $PROJECT/archives" | tee -a "$BATCH_LOG"
