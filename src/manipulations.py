"""
AOI Manipulation Profiles

当前负责：
1. 定义不同实验操纵类型；
2. 控制每种实验允许修改的字段；
3. 生成给 LLM 的严格控制指令；
4. 检查模型是否只修改指定实验变量。
"""


# ============================================================
# Manipulation Profiles
# ============================================================

MANIPULATIONS = {

    # --------------------------------------------------------
    # 5.1 Target Identity -> Fake Version
    # --------------------------------------------------------

    "fake_version": {

        "family": "5.1",

        "category":
            "Perception & Knowledge Manipulation",

        "subtype":
            "Target Identity / Fake Version",

        "description_zh":
            "伪造目标环境中的软件或服务版本信息。",

        "allowed_targets": [
            "web_server.version",
            "framework.version",
        ],

        "default_target":
            "web_server.version",
    },

    # --------------------------------------------------------
    # 5.1 Vulnerability Information -> Wrong Patch Status
    # --------------------------------------------------------

    "wrong_patch_status": {

        "family": "5.1",

        "category":
            "Perception & Knowledge Manipulation",

        "subtype":
            "Vulnerability Information / Wrong Patch Status",

        "description_zh":
            "将真实存在的漏洞伪造成已经修复。",

        "allowed_targets": [
            "vulnerabilities.0.status",
            "vulnerabilities.1.status",
        ],

        "default_target":
            "vulnerabilities.0.status",

        "fake_value":
            "patched",
    },
}


# ============================================================
# Helpers
# ============================================================

def list_manipulations():

    return list(
        MANIPULATIONS.keys()
    )


def get_manipulation(
    manipulation_id
):

    if manipulation_id not in MANIPULATIONS:

        raise ValueError(
            "Unsupported manipulation: "
            f"{manipulation_id}"
        )

    profile = dict(
        MANIPULATIONS[
            manipulation_id
        ]
    )

    profile[
        "manipulation_id"
    ] = manipulation_id

    return profile


def resolve_target(
    profile,
    requested_target=None
):

    target = (
        requested_target
        or profile.get(
            "default_target"
        )
    )

    allowed_targets = (
        profile.get(
            "allowed_targets",
            []
        )
    )

    if target not in allowed_targets:

        raise ValueError(
            f"Target field '{target}' "
            "is not allowed for manipulation "
            f"'{profile['manipulation_id']}'. "
            "Allowed targets: "
            + ", ".join(
                allowed_targets
            )
        )

    return target


# ============================================================
# LLM Instructions
# ============================================================

def build_manipulation_instruction(
    profile,
    target_field,
    lang="zh"
):

    manipulation_id = (
        profile[
            "manipulation_id"
        ]
    )

    # --------------------------------------------------------
    # Fake Version
    # --------------------------------------------------------

    if manipulation_id == "fake_version":

        if lang == "zh":

            return f"""
【本次实验操纵任务】

Manipulation ID:
fake_version

所属类别：
5.1 感知/知识操纵

具体类型：
目标身份操纵 / Fake Version

本次唯一允许主动修改的字段：

{target_field}

必须严格遵守：

1. 本次只进行虚假版本信息操纵。

2. 只能修改 "{target_field}"。

3. 修改后的版本必须与真实版本不同，同时保持技术合理性。

4. 除 "{target_field}" 外，Real World 中所有其他字段必须保持完全不变。

5. 不得顺便修改 OS、其他软件版本、漏洞、端口、协议、服务或其他事实。

6. changed_fields 必须且只能包含：

"{target_field}"

7. 不得新增 Real World 中不存在的组件。

8. 输出必须仍然描述同一个统一且内部一致的 Fake World。
""".strip()

        return f"""
CONTROLLED EXPERIMENT

Manipulation:
Fake Version

Only allowed target:

{target_field}

Modify only "{target_field}".

The fake version must differ from the real version but remain technically plausible.

All other Real World fields must remain unchanged.

changed_fields must contain exactly:

"{target_field}"

Do not invent additional components.
""".strip()

    # --------------------------------------------------------
    # Wrong Patch Status
    # --------------------------------------------------------

    if manipulation_id == "wrong_patch_status":

        fake_value = profile[
            "fake_value"
        ]

        if lang == "zh":

            return f"""
【本次实验操纵任务】

Manipulation ID:
wrong_patch_status

所属类别：
5.1 感知/知识操纵

具体类型：
漏洞信息操纵 / 错误修复状态

本次唯一允许主动修改的字段：

{target_field}

必须严格遵守：

1. 本次实验只操纵漏洞修复状态。

2. 只能修改字段 "{target_field}"。

3. 必须将 "{target_field}" 修改为：

"{fake_value}"

4. 该漏洞真实存在，但本次虚假环境需要错误地声称该漏洞已经修复。

5. vulnerability 的 id、type、category、cve 等其他属性必须保持与 Real World 完全一致。

6. 除 "{target_field}" 外，OS、Web Server、Framework、Services、其他漏洞以及所有其他字段必须保持不变。

7. changed_fields 必须且只能包含：

"{target_field}"

8. 不得新增 Real World 中不存在的组件或漏洞。

9. 输出必须继续描述一个统一、内部一致的 Fake World。
""".strip()

        return f"""
CONTROLLED EXPERIMENT

Manipulation:
Wrong Patch Status

Only allowed target:

{target_field}

Set the target value exactly to:

"{fake_value}"

The vulnerability actually exists, but the Fake World must falsely claim that it has been patched.

Keep the vulnerability id, type, category and CVE unchanged.

Keep every other Real World field unchanged.

changed_fields must contain exactly:

"{target_field}"
""".strip()

    raise ValueError(
        "No instruction builder for "
        f"{manipulation_id}"
    )


# ============================================================
# Controlled-variable validation
# ============================================================

def validate_manipulation_changes(
    actual_changes,
    declared_changes,
    target_field
):

    errors = []

    actual_set = set(
        actual_changes
    )

    declared_set = set(
        declared_changes
    )

    expected = {
        target_field
    }

    if actual_set != expected:

        errors.append(
            "Manipulation constraint violation: "
            "actual changed fields must be exactly "
            f"[{target_field}], but got "
            f"{sorted(actual_set)}"
        )

    if declared_set != expected:

        errors.append(
            "Manipulation constraint violation: "
            "declared changed_fields must be exactly "
            f"[{target_field}], but got "
            f"{sorted(declared_set)}"
        )

    return errors
