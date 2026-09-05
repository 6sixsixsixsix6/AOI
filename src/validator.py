import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_WORLD_PATH = PROJECT_ROOT / "configs/real_world.json"
FAKE_WORLD_PATH = PROJECT_ROOT / "configs/fake_world.json"


# ============================================================
# JSON helpers
# ============================================================

def load_json(path: Path) -> dict:
    """
    读取 JSON 文件。
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# changed_fields normalization
# ============================================================

def normalize_field_path(path: str) -> str:
    """
    统一字段路径格式。

    world.web_server.version
        ->
    web_server.version
    """

    path = path.strip()

    while path.startswith("world."):
        path = path[len("world."):]

    return path


# ============================================================
# Flatten JSON
# ============================================================

def flatten_json(data, prefix=""):
    """
    将嵌套 JSON 展平成：

    {
        "web_server.name": "Apache",
        "web_server.version": "2.4.51",
        "services.0.port": 80
    }

    方便比较 Real World 和 Fake World。
    """

    result = {}

    if isinstance(data, dict):

        for key, value in data.items():

            new_prefix = (
                f"{prefix}.{key}"
                if prefix
                else key
            )

            result.update(
                flatten_json(
                    value,
                    new_prefix
                )
            )

    elif isinstance(data, list):

        for index, value in enumerate(data):

            new_prefix = (
                f"{prefix}.{index}"
                if prefix
                else str(index)
            )

            result.update(
                flatten_json(
                    value,
                    new_prefix
                )
            )

    else:

        result[prefix] = data

    return result


# ============================================================
# Calculate actual changes
# ============================================================

def calculate_actual_changes(
    real_world: dict,
    fake_world: dict
):
    """
    自动比较 Real World 与 Fake World，
    返回实际上发生变化的字段。
    """

    # real_world 顶层有 environment_id，
    # 这个字段不属于 world 内容，因此去掉。
    real_content = {
        key: value
        for key, value in real_world.items()
        if key != "environment_id"
    }

    fake_content = fake_world.get(
        "world",
        {}
    )

    real_flat = flatten_json(
        real_content
    )

    fake_flat = flatten_json(
        fake_content
    )

    all_paths = (
        set(real_flat.keys())
        | set(fake_flat.keys())
    )

    changed = []

    for path in sorted(all_paths):

        real_value = real_flat.get(
            path,
            "__MISSING__"
        )

        fake_value = fake_flat.get(
            path,
            "__MISSING__"
        )

        if real_value != fake_value:
            changed.append(path)

    return changed


# ============================================================
# Internal consistency validation
# ============================================================

def validate_internal_consistency(
    fake_world: dict
):
    """
    检查 Fake World 内部是否存在明显冲突。
    """

    errors = []

    world = fake_world.get(
        "world",
        {}
    )

    # --------------------------------------------------------
    # Web Server vs HTTP/HTTPS Service
    # --------------------------------------------------------

    web_server = world.get(
        "web_server",
        {}
    )

    web_server_name = web_server.get(
        "name"
    )

    services = world.get(
        "services",
        []
    )

    for service in services:

        protocol = service.get(
            "protocol"
        )

        service_name = service.get(
            "service"
        )

        port = service.get(
            "port"
        )

        if protocol in (
            "http",
            "https"
        ):

            if (
                web_server_name
                and service_name
                and
                web_server_name.lower()
                != service_name.lower()
            ):

                errors.append(
                    "Web server conflict: "
                    f"web_server.name="
                    f"{web_server_name}, "
                    f"but service on port "
                    f"{port} is "
                    f"{service_name}"
                )

    return errors


# ============================================================
# changed_fields validation
# ============================================================

def validate_changed_fields(
    real_world: dict,
    fake_world: dict
):
    """
    检查模型声明的 changed_fields
    是否和实际修改完全一致。
    """

    errors = []

    # 程序自己算出真正修改的字段
    actual_changes = (
        calculate_actual_changes(
            real_world,
            fake_world
        )
    )

    # 模型声明的字段
    declared_changes = (
        fake_world.get(
            "changed_fields",
            []
        )
    )

    declared_changes = [
        normalize_field_path(field)
        for field in declared_changes
        if isinstance(field, str)
    ]

    # 去重
    declared_changes = sorted(
        set(declared_changes)
    )

    actual_changes = sorted(
        set(actual_changes)
    )

    actual_set = set(
        actual_changes
    )

    declared_set = set(
        declared_changes
    )

    # --------------------------------------------------------
    # 实际改了，但模型没声明
    # --------------------------------------------------------

    missing_declarations = (
        actual_set
        - declared_set
    )

    for field in sorted(
        missing_declarations
    ):

        errors.append(
            "Changed field not declared: "
            f"{field}"
        )

    # --------------------------------------------------------
    # 模型说改了，但实际没改
    # --------------------------------------------------------

    false_declarations = (
        declared_set
        - actual_set
    )

    for field in sorted(
        false_declarations
    ):

        errors.append(
            "Declared changed field "
            "was not actually changed: "
            f"{field}"
        )

    return (
        errors,
        actual_changes,
        declared_changes
    )


# ============================================================
# Metadata validation
# ============================================================

def validate_metadata(
    real_world: dict,
    fake_world: dict
):
    """
    检查 Fake World 的基础元信息。
    """

    errors = []

    real_environment_id = (
        real_world.get(
            "environment_id"
        )
    )

    source_environment_id = (
        fake_world.get(
            "source_environment_id"
        )
    )

    if (
        real_environment_id
        != source_environment_id
    ):

        errors.append(
            "source_environment_id mismatch: "
            f"expected "
            f"{real_environment_id}, "
            f"got "
            f"{source_environment_id}"
        )

    if "world" not in fake_world:

        errors.append(
            "Missing top-level field: world"
        )

    if "changed_fields" not in fake_world:

        errors.append(
            "Missing top-level field: "
            "changed_fields"
        )

    return errors


# ============================================================
# Main validation
# ============================================================

def validate_fake_world(
    real_world: dict,
    fake_world: dict
):
    """
    执行完整 Fake World 检查。
    """

    errors = []

    # 1. Metadata
    errors.extend(
        validate_metadata(
            real_world,
            fake_world
        )
    )

    # 2. 内部一致性
    errors.extend(
        validate_internal_consistency(
            fake_world
        )
    )

    # 3. changed_fields 对账
    (
        changed_field_errors,
        actual_changes,
        declared_changes
    ) = validate_changed_fields(
        real_world,
        fake_world
    )

    errors.extend(
        changed_field_errors
    )

    return (
        errors,
        actual_changes,
        declared_changes
    )


# ============================================================
# Program entry
# ============================================================

def main():

    real_world = load_json(
        REAL_WORLD_PATH
    )

    fake_world = load_json(
        FAKE_WORLD_PATH
    )

    (
        errors,
        actual_changes,
        declared_changes
    ) = validate_fake_world(
        real_world,
        fake_world
    )

    print(
        "=== Fake World Validation ==="
    )

    print()

    print(
        "Actual changed fields:"
    )

    if actual_changes:

        for field in actual_changes:
            print(
                f"- {field}"
            )

    else:

        print(
            "- None"
        )

    print()

    print(
        "Declared changed fields:"
    )

    if declared_changes:

        for field in declared_changes:
            print(
                f"- {field}"
            )

    else:

        print(
            "- None"
        )

    print()

    if errors:

        print(
            "=== Validation Failed ==="
        )

        for error in errors:

            print(
                f"- {error}"
            )

    else:

        print(
            "=== Validation Passed ==="
        )

        print(
            "Fake World is internally "
            "consistent and changed_fields "
            "matches the actual changes."
        )


if __name__ == "__main__":
    main()
