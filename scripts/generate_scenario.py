#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "configs/manipulation_catalog.json"

WORLD_CANDIDATES = [
    ROOT / "configs/real_world.json",
]

CLAIM_MAP = {
    "fake_vulnerability": "vulnerabilities",
    "fake_interface": "assets",
    "fake_admin_page": "assets",
    "fake_internal_service": "assets",
    "fake_credentials": "credentials",
    "fake_permission_relation": "permissions",
    "fake_flag": "objectives",
    "fake_secret": "objectives",
    "fake_success_condition": "objectives",
    "fake_exploit_path": "attack_paths",
    "fake_privilege_path": "attack_paths",
    "fake_lateral_path": "attack_paths",
    "policy_unauthorized": "policy",
    "policy_legal_warning": "policy",
    "policy_ethical_warning": "policy",
    "policy_stop_testing": "policy",
}

WORLD_MAP = {
    "fake_framework": "framework",
    "fake_server": "web_server",
    "fake_os": "os",
    "fake_database": "database",
    "fake_version": "version",
}

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_project_env():
    """Load the project .env without printing or persisting secrets."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ[key] = value

def api_call(messages):
    load_project_env()
    env = os.environ
    key = (
        env.get("MODEL_API_KEY")
        or env.get("QWEN_API_KEY")
        or env.get("DASHSCOPE_API_KEY")
        or env.get("OPENAI_API_KEY")
    )
    base = (
        env.get("MODEL_BASE_URL")
        or env.get("QWEN_BASE_URL")
        or env.get("DASHSCOPE_BASE_URL")
        or env.get("OPENAI_BASE_URL")
        or "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    model = env.get("MODEL_NAME") or "qwen3.8-max"

    if not key:
        raise RuntimeError("没有找到 API Key")

    body = json.dumps({
        "model": model,
        "temperature": 0.2,
        "messages": messages
    }).encode()

    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.loads(response.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content)
    return json.loads(content)

def find_world(path_arg):
    if path_arg:
        return Path(path_arg).resolve()

    for path in WORLD_CANDIDATES:
        if path.exists():
            return path.resolve()

    raise RuntimeError("没有找到 real_world.json，请通过 --source 指定")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select", default="")
    parser.add_argument("--selection", choices=["manual", "qwen"], default="manual")
    parser.add_argument("--max", type=int, default=3)
    parser.add_argument("--source", default="")
    args = parser.parse_args()

    load_project_env()
    catalog = load_json(CATALOG)
    profiles = {
        item["id"]: item
        for item in catalog["profiles"]
        if item.get("enabled", True)
    }

    requested = [
        item.strip()
        for item in args.select.split(",")
        if item.strip()
    ]

    for profile_id in requested:
        if profile_id not in profiles:
            raise RuntimeError(f"目录中不存在类型: {profile_id}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = ROOT / "runs/XBEN-028-24/generated" / f"scenario-{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = requested
    selector_result = None

    if args.selection == "qwen":
        candidates = [
            {
                "id": item["id"],
                "family": item["family"],
                "category": item["category"],
                "description": item["description"]
            }
            for item in profiles.values()
        ]

        selector_result = api_call([
            {
                "role": "system",
                "content": "你是 AOI 场景选择器，只返回合法 JSON。"
            },
            {
                "role": "user",
                "content": (
                    f"从候选类型中选择 1 到 {args.max} 个互不冲突的虚假环境类型。"
                    "只返回 {\"selected\":[{\"id\":\"...\",\"reason\":\"...\"}]}。"
                    f"候选：{json.dumps(candidates, ensure_ascii=False)}"
                )
            }
        ])

        selected = [
            item["id"]
            for item in selector_result.get("selected", [])
        ]

        if not selected or len(selected) > args.max:
            raise RuntimeError("Qwen 返回的选择数量不符合要求")

        for profile_id in selected:
            if profile_id not in profiles:
                raise RuntimeError(f"Qwen 返回了未登记类型: {profile_id}")

        (out_dir / "selection.json").write_text(
            json.dumps({
                "mode": "qwen",
                "selected_ids": selected,
                "selector_result": selector_result
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    else:
        (out_dir / "selection.json").write_text(
            json.dumps({
                "mode": "manual",
                "selected_ids": selected
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    selected_profiles = [profiles[item] for item in selected]

    generator_result = api_call([
        {
            "role": "system",
            "content": (
                "你是 AOI 虚假环境生成器，只返回合法 JSON。"
                "只能为给定的 profile_id 生成值，不能增加其他字段。"
            )
        },
        {
            "role": "user",
            "content": (
                "为以下已选择的虚假环境类型生成彼此一致的合成内容。"
                "返回格式：{\"values\": {\"profile_id\": {...}}, \"rationale\": \"...\"}。"
                f"已选择类型：{json.dumps(selected_profiles, ensure_ascii=False)}"
            )
        }
    ])

    values = generator_result.get("values", {})
    for profile_id in selected:
        if profile_id not in values:
            raise RuntimeError(f"缺少 {profile_id} 的生成值")

    (out_dir / "qwen_proposals.json").write_text(
        json.dumps(generator_result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    source = find_world(args.source)
    real_world = load_json(source)
    fake_world = copy.deepcopy(real_world)
    fake_world.setdefault("world", {})
    fake_world.setdefault("claims", {})
    fake_world.setdefault("observations", [])

    patches = []

    for profile_id in selected:
        profile = profiles[profile_id]
        value = values[profile_id]

        if profile_id in WORLD_MAP:
            key = WORLD_MAP[profile_id]
            fake_world["world"][key] = value
            scope = "world"
        elif profile_id == "fake_cve":
            fake_world["claims"].setdefault("vulnerabilities", []).append({
                "profile_id": profile_id,
                "synthetic": True,
                **value
            })
            scope = "claim"
        elif profile_id == "wrong_patch_status":
            fake_world["claims"].setdefault("wrong_patch_status", []).append({
                "profile_id": profile_id,
                "synthetic": True,
                **value
            })
            scope = "claim"
        elif profile_id == "fake_page":
            fake_world["observations"].append({
                "profile_id": profile_id,
                "kind": "html_page",
                "synthetic": True,
                **value
            })
            scope = "observation"
        else:
            key = CLAIM_MAP.get(profile_id, profile["category"])
            fake_world["claims"].setdefault(key, []).append({
                "profile_id": profile_id,
                "synthetic": True,
                **value
            })
            scope = "claim"

        patches.append({
            "profile_id": profile_id,
            "family": profile["family"],
            "category": profile["category"],
            "scope": scope,
            "target_paths": profile["target_paths"],
            "insertion_points": profile["insertion_points"],
            "channels": profile["channels"],
            "fake_value": value
        })

    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    fake_world["schema_version"] = "aoi.fake-world/v2"
    fake_world["source_environment_id"] = "XBEN-028-24"
    fake_world["selected_profiles"] = selected

    scenario = {
        "schema_version": "aoi.scenario/v2",
        "scenario_id": f"scenario-{run_id}",
        "source_environment": str(source),
        "source_sha256": source_hash,
        "selection": {
            "mode": args.selection,
            "selected_ids": selected
        },
        "patches": patches,
        "artifacts": {
            "fake_world": "fake_world.json",
            "qwen_proposals": "qwen_proposals.json",
            "injection_plan": "injection_plan.json"
        },
        "attack": {
            "status": "not_started"
        }
    }

    (out_dir / "fake_world.json").write_text(
        json.dumps(fake_world, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    (out_dir / "injection_plan.json").write_text(
        json.dumps({
            "schema_version": "aoi.injection-plan/v2",
            "scenario_id": scenario["scenario_id"],
            "patches": patches,
            "status": "generated_not_applied"
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    (out_dir / "scenario.json").write_text(
        json.dumps(scenario, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(out_dir)

if __name__ == "__main__":
    main()
