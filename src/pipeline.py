import argparse
import copy
import json
import os
import re
import shutil
import sys
import time
import traceback

from datetime import datetime
from pathlib import Path

from generator import (
    REAL_WORLD_PATH,
    FAKE_WORLD_PATH,
    RAW_RESPONSE_PATH,
    load_json,
    load_prompt,
    generate_fake_world,
    normalize_changed_fields,
    validate_basic_schema,
    save_json,
)

from validator import validate_fake_world

from renderer import (
    render_html,
    render_http_header,
    render_nmap,
    save_outputs,
)

from vulnerability_renderer import (
    save_vulnerability_api,
)

from manipulations import (
    list_manipulations,
    get_manipulation,
    resolve_target,
    build_manipulation_instruction,
    validate_manipulation_changes,
)


# ============================================================
# Run Logger configuration
# ============================================================

RUNS_ROOT = Path("runs")
OUTPUTS_DIR = Path("outputs")


# ============================================================
# Basic helpers
# ============================================================

def print_fields(title, fields):
    """
    打印字段列表。
    """
    print(title)

    if fields:
        for field in fields:
            print(f"- {field}")
    else:
        print("- None")

    print()


def safe_name(value):
    """
    将 environment_id 转换为适合作为目录名的字符串。
    """
    value = str(value or "unknown-environment")

    return re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        value
    )


def current_time_iso():
    """
    返回带本地时区的 ISO 时间。
    """
    return (
        datetime.now()
        .astimezone()
        .isoformat(timespec="seconds")
    )


def file_signature(path):
    """
    获取文件签名。

    用于判断文件是否在本次运行中被重新生成。
    """
    path = Path(path)

    if not path.exists() or not path.is_file():
        return None

    stat = path.stat()

    return (
        stat.st_mtime_ns,
        stat.st_size
    )


def snapshot_directory(directory):
    """
    保存目录中现有文件的签名。
    """
    directory = Path(directory)

    result = {}

    if not directory.exists():
        return result

    for path in directory.iterdir():

        if path.is_file():
            result[path.name] = file_signature(path)

    return result


def copy_if_changed(
    source,
    old_signature,
    destination
):
    """
    仅当文件在本次运行中发生变化时进行归档。
    """
    source = Path(source)
    destination = Path(destination)

    if not source.exists():
        return False

    new_signature = file_signature(source)

    if new_signature == old_signature:
        return False

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    shutil.copy2(
        source,
        destination
    )

    return True


def archive_changed_outputs(
    before_signatures,
    run_dir
):
    """
    归档本次 Renderer 新生成或修改的 Observation 文件。

    自动扫描 outputs/，
    因此以后新增 Renderer 后也能自动留档。
    """
    archived = []

    if not OUTPUTS_DIR.exists():
        return archived

    destination_dir = (
        run_dir
        / "observations"
    )

    destination_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    for path in sorted(
        OUTPUTS_DIR.iterdir()
    ):

        if not path.is_file():
            continue

        # API 原始响应单独保存，
        # 不放入 observations。
        if path.name == RAW_RESPONSE_PATH.name:
            continue

        old_signature = (
            before_signatures.get(
                path.name
            )
        )

        new_signature = (
            file_signature(path)
        )

        if new_signature == old_signature:
            continue

        destination = (
            destination_dir
            / path.name
        )

        shutil.copy2(
            path,
            destination
        )

        archived.append(
            path.name
        )

    return archived


# ============================================================
# Run Logger
# ============================================================

def create_run(
    environment_id,
    args
):
    """
    为本次实验建立独立 Run 目录。
    """

    run_id = (
        datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S_%f"
        )
    )

    environment_name = safe_name(
        environment_id
    )

    run_dir = (
        RUNS_ROOT
        / environment_name
        / run_id
    )

    run_dir.mkdir(
        parents=True,
        exist_ok=False
    )

    metadata = {
        "run_id": run_id,
        "environment_id": environment_id,
        "status": "running",
        "started_at": current_time_iso(),
        "completed_at": None,
        "duration_seconds": None,
        "prompt_language": args.lang,
        "generation_mode": args.mode,
        "model": None,
        "base_url": None,
        "changed_fields": [],
        "observation_files": [],
        "error": None
    }

    save_json(
        metadata,
        run_dir / "metadata.json"
    )

    return (
        run_id,
        run_dir,
        metadata
    )


def save_metadata(
    metadata,
    run_dir
):
    """
    保存本次 Run 的 metadata.json。
    """

    save_json(
        metadata,
        run_dir / "metadata.json"
    )


def finish_metadata(
    metadata,
    run_dir,
    started_monotonic,
    status
):
    """
    更新运行结束状态。
    """

    metadata["status"] = status
    metadata["completed_at"] = (
        current_time_iso()
    )

    metadata["duration_seconds"] = round(
        time.perf_counter()
        - started_monotonic,
        3
    )

    save_metadata(
        metadata,
        run_dir
    )


# ============================================================
# Main Pipeline
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="AOI Fake World Pipeline"
    )

    parser.add_argument(
        "--lang",
        choices=["zh", "en"],
        default="zh",
        help="Prompt language"
    )

    parser.add_argument(
        "--mode",
        choices=["mock", "api"],
        default="mock",
        help="Generation mode"
    )

    parser.add_argument(
        "--manipulation",
        choices=list_manipulations(),
        default="fake_version",
        help="Controlled AOI manipulation type"
    )

    parser.add_argument(
        "--target",
        default=None,
        help="Target field for the manipulation"
    )

    args = parser.parse_args()

    manipulation_profile = (
        get_manipulation(
            args.manipulation
        )
    )

    target_field = (
        resolve_target(
            manipulation_profile,
            args.target
        )
    )

    manipulation_instruction = (
        build_manipulation_instruction(
            manipulation_profile,
            target_field,
            args.lang
        )
    )

    print("=" * 58)
    print("AOI Fake World Pipeline")
    print("=" * 58)
    print()

    print(f"Language: {args.lang}")
    print(f"Mode: {args.mode}")
    print(
        f"Manipulation: "
        f"{args.manipulation}"
    )
    print(
        f"Target field: "
        f"{target_field}"
    )
    print()

    # ========================================================
    # Stage 1: Load Real World
    # ========================================================

    print(
        "=== Stage 1: Load Real World ==="
    )

    real_world = load_json(
        REAL_WORLD_PATH
    )

    environment_id = (
        real_world.get(
            "environment_id",
            "unknown-environment"
        )
    )

    print(
        f"Environment ID: "
        f"{environment_id}"
    )

    print()

    # ========================================================
    # Create Run Logger
    # ========================================================

    (
        run_id,
        run_dir,
        metadata
    ) = create_run(
        environment_id,
        args
    )

    metadata[
        "manipulation"
    ] = args.manipulation

    metadata[
        "manipulation_family"
    ] = manipulation_profile[
        "family"
    ]

    metadata[
        "manipulation_subtype"
    ] = manipulation_profile[
        "subtype"
    ]

    metadata[
        "target_field"
    ] = target_field

    (
        run_dir
        / "manipulation_instruction.txt"
    ).write_text(
        manipulation_instruction,
        encoding="utf-8"
    )

    save_metadata(
        metadata,
        run_dir
    )

    started_monotonic = (
        time.perf_counter()
    )

    print(
        f"Run ID: {run_id}"
    )

    print(
        f"Run archive: {run_dir}"
    )

    print()

    # 保存真实环境原文件
    shutil.copy2(
        REAL_WORLD_PATH,
        run_dir / "real_world.json"
    )

    # 保存 API Response 运行前状态，
    # 避免误归档上一次运行留下的响应。
    raw_response_before = (
        file_signature(
            RAW_RESPONSE_PATH
        )
    )

    try:

        # ====================================================
        # Stage 2: Load Prompt
        # ====================================================

        print(
            "=== Stage 2: Load Prompt ==="
        )

        prompt = load_prompt(
            args.lang
        )

        print(
            f"Prompt language: "
            f"{args.lang}"
        )

        print()

        (
            run_dir
            / "prompt_used.txt"
        ).write_text(
            prompt,
            encoding="utf-8"
        )

        # ====================================================
        # Stage 3: Generate Fake World
        # ====================================================

        print(
            "=== Stage 3: Generate Fake World ==="
        )

        fake_world = (
            generate_fake_world(
                real_world=real_world,
                prompt=prompt,
                mode=args.mode,
                manipulation_instruction=manipulation_instruction
            )
        )

        # --------------------------------------------
        # 保存模型原始生成的 Fake World
        # --------------------------------------------

        save_json(
            copy.deepcopy(fake_world),
            run_dir
            / "fake_world_model_output.json"
        )

        # --------------------------------------------
        # 如果是 API 模式，
        # 保存本次完整 API Response
        # --------------------------------------------

        if args.mode == "api":

            copy_if_changed(
                RAW_RESPONSE_PATH,
                raw_response_before,
                run_dir
                / "api_response.json"
            )

            # generator 已完成 API 配置加载，
            # 此时可安全记录模型和 Base URL。
            # 注意：绝不记录 API Key。
            metadata["model"] = (
                os.getenv(
                    "MODEL_NAME"
                )
            )

            metadata["base_url"] = (
                os.getenv(
                    "MODEL_BASE_URL"
                )
            )

        else:

            metadata["model"] = "mock"
            metadata["base_url"] = None

        # --------------------------------------------
        # 统一 changed_fields
        # --------------------------------------------

        fake_world = (
            normalize_changed_fields(
                fake_world
            )
        )

        # --------------------------------------------
        # 基本 Schema 检查
        # --------------------------------------------

        validate_basic_schema(
            fake_world=fake_world,
            real_world=real_world
        )

        # --------------------------------------------
        # 保存当前工作区 Fake World
        # --------------------------------------------

        save_json(
            fake_world,
            FAKE_WORLD_PATH
        )

        # --------------------------------------------
        # 保存本 Run Fake World
        # --------------------------------------------

        save_json(
            fake_world,
            run_dir
            / "fake_world.json"
        )

        metadata["changed_fields"] = (
            fake_world.get(
                "changed_fields",
                []
            )
        )

        save_metadata(
            metadata,
            run_dir
        )

        print(
            f"Fake World saved to: "
            f"{FAKE_WORLD_PATH}"
        )

        print()

        # ====================================================
        # Stage 4: Validate Fake World
        # ====================================================

        print(
            "=== Stage 4: Validate Fake World ==="
        )

        (
            errors,
            actual_changes,
            declared_changes
        ) = validate_fake_world(
            real_world,
            fake_world
        )

        print()

        print_fields(
            "Actual changed fields:",
            actual_changes
        )

        print_fields(
            "Declared changed fields:",
            declared_changes
        )

        # --------------------------------------------
        # Controlled Manipulation Validation
        # --------------------------------------------

        errors = list(errors)

        manipulation_errors = (
            validate_manipulation_changes(
                actual_changes,
                declared_changes,
                target_field
            )
        )

        errors.extend(
            manipulation_errors
        )

        validation_record = {
            "passed": not bool(errors),
            "errors": errors,
            "actual_changed_fields": (
                actual_changes
            ),
            "declared_changed_fields": (
                declared_changes
            )
        }

        save_json(
            validation_record,
            run_dir
            / "validation.json"
        )

        if errors:

            print(
                "=== Validation Failed ==="
            )

            for error in errors:
                print(f"- {error}")

            print()

            print(
                "Pipeline stopped "
                "before rendering."
            )

            metadata["error"] = {
                "stage": "validation",
                "message": (
                    "Fake World validation failed."
                ),
                "details": errors
            }

            finish_metadata(
                metadata,
                run_dir,
                started_monotonic,
                "validation_failed"
            )

            print()
            print(
                f"Run archive preserved at: "
                f"{run_dir}"
            )

            return 1

        print(
            "=== Validation Passed ==="
        )

        print(
            "Fake World passed "
            "consistency checks."
        )

        print()

        # ====================================================
        # Stage 5: Render Observations
        # ====================================================

        print(
            "=== Stage 5: Render Observations ==="
        )

        # 保存渲染前 outputs 状态。
        # 后面只归档本次真正发生变化的文件。
        outputs_before = (
            snapshot_directory(
                OUTPUTS_DIR
            )
        )

        world = fake_world["world"]

        html = render_html(
            world
        )

        header = render_http_header(
            world
        )

        nmap = render_nmap(
            world
        )

        save_outputs(
            html=html,
            header=header,
            nmap=nmap,
            fake_world=fake_world
        )

        # Vulnerability manipulation requires
        # an Observation Source that actually
        # exposes the manipulated vulnerability fact.
        if args.manipulation == "wrong_patch_status":

            save_vulnerability_api(
                world
            )

            print(
                "Vulnerability API observation "
                "saved to "
                "outputs/api_vulnerabilities.json"
            )

        archived_outputs = (
            archive_changed_outputs(
                outputs_before,
                run_dir
            )
        )

        metadata[
            "observation_files"
        ] = archived_outputs

        print(
            "Rendered observations saved "
            "to outputs/"
        )

        print()

        # ====================================================
        # Success
        # ====================================================

        finish_metadata(
            metadata,
            run_dir,
            started_monotonic,
            "success"
        )

        print("=" * 58)
        print(
            "Pipeline Completed Successfully"
        )
        print("=" * 58)
        print()

        print(
            f"Fake World: "
            f"{FAKE_WORLD_PATH}"
        )

        print(
            "Rendered outputs: outputs/"
        )

        print(
            f"Run archive: {run_dir}"
        )

        print()

        return 0

    except Exception as e:

        # ====================================================
        # Failure Logger
        # ====================================================

        # API Response 如果本轮已经产生，
        # 即使后续失败也尽量保存。
        if args.mode == "api":

            copy_if_changed(
                RAW_RESPONSE_PATH,
                raw_response_before,
                run_dir
                / "api_response.json"
            )

        error_trace = (
            traceback.format_exc()
        )

        (
            run_dir
            / "error.txt"
        ).write_text(
            error_trace,
            encoding="utf-8"
        )

        metadata["error"] = {
            "type": type(e).__name__,
            "message": str(e)
        }

        # 尽可能记录当前模型信息，
        # 但绝不保存 API Key。
        if args.mode == "api":

            metadata["model"] = (
                os.getenv(
                    "MODEL_NAME"
                )
            )

            metadata["base_url"] = (
                os.getenv(
                    "MODEL_BASE_URL"
                )
            )

        finish_metadata(
            metadata,
            run_dir,
            started_monotonic,
            "failed"
        )

        print()
        print(
            f"Run archive preserved at: "
            f"{run_dir}"
        )

        raise


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    try:

        exit_code = main()

        sys.exit(
            exit_code
        )

    except Exception as e:

        print()
        print(
            "=== Pipeline Error ==="
        )

        print(
            str(e)
        )

        sys.exit(1)
