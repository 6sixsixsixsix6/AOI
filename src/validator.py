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


def _claim_changes(fake_world):
    """Return canonical paths represented by synthetic observation claims.

    The original validator predates the observation bundle and compared only
    ``fake_world.world`` with the real fixture.  New scenarios keep claims
    separately, so those paths need to participate in the same reconciliation
    without pretending that a claim changed the source world itself.
    """

    if not isinstance(fake_world, dict):
        return set()
    claims = fake_world.get("claims", {})
    if not isinstance(claims, dict):
        claims = {}
    changes = set()
    selected_profiles = fake_world.get("selected_profiles")
    new_contract = isinstance(selected_profiles, list)
    selected_set = set(selected_profiles) if new_contract else set()

    def add_list(path, value):
        if isinstance(value, list) and value:
            changes.add(f"{path}[]")
        elif value is not None and not isinstance(value, list):
            changes.add(path)

    def target_path(raw_target):
        if not isinstance(raw_target, str) or not raw_target.strip():
            return None
        target = raw_target.strip()
        for prefix in ("world.", "claims."):
            if target.startswith(prefix):
                target = target[len(prefix):]
        if not target.startswith("security_assessment."):
            target = f"security_assessment.{target}"
        return target

    # Vulnerability and patch claims may describe a precise API field.  The
    # v2 scenario format records that precise path; legacy claim-only data is
    # represented by the category wildcard instead.
    vulnerability_claims = claims.get("vulnerabilities")
    if isinstance(vulnerability_claims, list) and vulnerability_claims:
        for claim in vulnerability_claims:
            if not isinstance(claim, dict):
                continue
            raw_target = claim.get("target") or claim.get("target_path") or claim.get("path")
            target = target_path(raw_target)
            if (
                target is None
                and new_contract
                and (claim.get("profile_id") == "fake_cve" or "fake_cve" in selected_set)
                and (claim.get("cve") or claim.get("fake_cve"))
            ):
                claim_id = claim.get("id") or claim.get("vulnerability_id")
                if claim_id is not None and str(claim_id).strip():
                    target = f"security_assessment.vulnerabilities[{str(claim_id).strip()}].cve"
            if target is not None:
                changes.add(target)
            else:
                changes.add("claims.vulnerabilities[]")
    elif vulnerability_claims is not None:
        add_list("claims.vulnerabilities", vulnerability_claims)

    patch_claims = claims.get("wrong_patch_status")
    if isinstance(patch_claims, list) and patch_claims:
        for claim in patch_claims:
            if not isinstance(claim, dict):
                continue
            target = target_path(claim.get("target") or claim.get("target_path") or claim.get("path"))
            if target is not None:
                changes.add(target)
            else:
                changes.add("claims.wrong_patch_status[]")
    elif patch_claims is not None:
        add_list("claims.wrong_patch_status", patch_claims)

    for category, value in claims.items():
        if category in {"vulnerabilities", "wrong_patch_status"}:
            continue
        base = f"claims.{category}"
        if isinstance(value, dict):
            for key, item in value.items():
                add_list(f"{base}.{key}", item)
        else:
            add_list(base, value)

    observations = fake_world.get("observations")
    if isinstance(observations, list) and observations:
        changes.add("observations.pages[]")
    return changes


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
    if not isinstance(real_world, dict):
        real_world = {}
    if not isinstance(fake_world, dict):
        fake_world = {}

    real_content = {
        key: value
        for key, value in real_world.items()
        if key != "environment_id"
    }

    fake_content = fake_world.get(
        "world",
        {}
    )
    if not isinstance(fake_content, dict):
        fake_content = {}

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

    # Claims and generated observation pages have no counterpart in the real
    # world fixture, so reconcile them as synthetic additions.  World paths
    # above intentionally retain their legacy spelling for compatibility.
    changed.extend(sorted(_claim_changes(fake_world)))

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

    if not isinstance(fake_world, dict):
        return ["fake_world must be a JSON object"]

    world = fake_world.get("world", {})
    if not isinstance(world, dict):
        return ["world must be a JSON object"]

    # --------------------------------------------------------
    # Web Server vs HTTP/HTTPS Service
    # --------------------------------------------------------

    web_server = world.get("web_server", {})
    if web_server is None:
        web_server = {}
    if not isinstance(web_server, dict):
        errors.append("web_server must be a JSON object")
        web_server = {}

    web_server_name = web_server.get(
        "name"
    )

    services = world.get("services", [])
    if services is None:
        services = []
    if not isinstance(services, list):
        errors.append("services must be a JSON array")
        services = []

    for index, service in enumerate(services):

        if not isinstance(service, dict):
            errors.append(f"services[{index}] must be a JSON object")
            continue

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
                str(web_server_name).lower()
                != str(service_name).lower()
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
    if not isinstance(fake_world, dict):
        return (
            ["fake_world must be a JSON object"],
            calculate_actual_changes(real_world, {}),
            []
        )

    # 程序自己算出真正修改的字段
    actual_changes = (
        calculate_actual_changes(
            real_world,
            fake_world
        )
    )

    # 模型声明的字段
    declared_value = (
        fake_world.get(
            "changed_fields",
            []
        )
    )
    if not isinstance(declared_value, list):
        errors.append("changed_fields must be a JSON array")
        declared_value = []

    declared_changes = []
    for index, field in enumerate(declared_value):
        if not isinstance(field, str) or not field.strip():
            errors.append(f"changed_fields[{index}] must be a non-empty string")
            continue
        declared_changes.append(normalize_field_path(field))

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

    if not isinstance(real_world, dict):
        errors.append("real_world must be a JSON object")
        real_world = {}
    if not isinstance(fake_world, dict):
        return errors + ["fake_world must be a JSON object"]

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

    if not isinstance(real_environment_id, str) or not real_environment_id.strip():
        errors.append("real_world.environment_id must be a non-empty string")
    if not isinstance(source_environment_id, str) or not source_environment_id.strip():
        errors.append("source_environment_id must be a non-empty string")

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
    elif not isinstance(fake_world["world"], dict):
        errors.append("world must be a JSON object")

    if "changed_fields" not in fake_world:

        errors.append(
            "Missing top-level field: "
            "changed_fields"
        )
    elif not isinstance(fake_world["changed_fields"], list):
        errors.append("changed_fields must be a JSON array")

    if "claims" in fake_world and not isinstance(fake_world["claims"], dict):
        errors.append("claims must be a JSON object")
    if "observations" in fake_world and not isinstance(fake_world["observations"], list):
        errors.append("observations must be a JSON array")

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
