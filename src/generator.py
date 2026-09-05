import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


# ============================================================
# Paths
# ============================================================

REAL_WORLD_PATH = Path("configs/real_world.json")
FAKE_WORLD_PATH = Path("configs/fake_world.json")

PROMPT_PATHS = {
    "zh": Path("prompts/fake_world_generator_zh.txt"),
    "en": Path("prompts/fake_world_generator_en.txt"),
}

RAW_RESPONSE_PATH = Path("outputs/last_api_response.json")


# ============================================================
# Basic file helpers
# ============================================================

def load_json(path: Path) -> dict:
    """
    读取 JSON 文件。
    """
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    """
    保存 JSON 文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )


def load_prompt(lang: str) -> str:
    """
    根据语言选择中文或英文 Prompt。
    """
    prompt_path = PROMPT_PATHS[lang]

    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_path}"
        )

    return prompt_path.read_text(
        encoding="utf-8"
    )


# ============================================================
# Mock generator
# ============================================================

def change_version(
    obj: dict,
    key: str,
    new_version: str,
    changed_fields: list,
    field_path: str
) -> None:
    """
    修改指定版本字段，并记录 changed_fields。
    """

    if key not in obj:
        return

    old_version = obj[key]

    if old_version != new_version:
        obj[key] = new_version
        changed_fields.append(field_path)



def _mock_alternate_version(value) -> str:
    """Generate a deterministic nearby version for Mock mode."""

    text = str(value).strip()
    match = re.match(r"^(.*?)(\d+)([^\d]*)$", text)

    if match:
        prefix, number, suffix = match.groups()
        number_value = int(number)
        alternate = number_value - 1 if number_value > 0 else 1
        return f"{prefix}{alternate}{suffix}"

    return f"{text}-alt"


def _get_mock_target(world: dict, target_field: str):
    """Read a dotted target path from the world object."""

    parts = target_field.split(".")
    current = world

    if not parts or any(not part for part in parts):
        raise ValueError(f"Invalid target field: {target_field}")

    for part in parts:
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(part)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(
                f"Target field does not exist: {target_field}"
            ) from exc

    return current


def _set_mock_target(world: dict, target_field: str, value):
    """Set a dotted target path and return its previous value."""

    parts = target_field.split(".")
    current = world

    if not parts or any(not part for part in parts):
        raise ValueError(f"Invalid target field: {target_field}")

    for part in parts[:-1]:
        try:
            if isinstance(current, list):
                current = current[int(part)]
            elif isinstance(current, dict):
                current = current[part]
            else:
                raise KeyError(part)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError(
                f"Target field does not exist: {target_field}"
            ) from exc

    final_part = parts[-1]

    try:
        if isinstance(current, list):
            index = int(final_part)
            old_value = current[index]
            current[index] = value
        elif isinstance(current, dict):
            old_value = current[final_part]
            current[final_part] = value
        else:
            raise KeyError(final_part)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValueError(
            f"Target field does not exist: {target_field}"
        ) from exc

    return old_value

def build_mock_fake_world(
    real_world: dict,
    manipulation_id: str = None,
    target_field: str = None
) -> dict:
    """
    本地 Mock Generator。

    不调用 LLM。
    主要用于验证整个程序流程。
    """

    world = copy.deepcopy(real_world)

    environment_id = world.pop(
        "environment_id",
        "unknown-environment"
    )

    changed_fields = []

    if manipulation_id:
        if not target_field:
            raise ValueError(
                "target_field is required for controlled mock generation"
            )

        old_value = _get_mock_target(
            world,
            target_field
        )

        if manipulation_id == "fake_version":
            new_value = _mock_alternate_version(old_value)
        elif manipulation_id == "wrong_patch_status":
            new_value = "patched"
        else:
            raise ValueError(
                f"Unsupported mock manipulation: {manipulation_id}"
            )

        if old_value == new_value:
            raise ValueError(
                f"Mock manipulation did not change target: {target_field}"
            )

        _set_mock_target(
            world,
            target_field,
            new_value
        )

        changed_fields.append(target_field)

        return {
            "fake_world_id": (
                f"fake-{environment_id}-{manipulation_id}"
            ),
            "source_environment_id": environment_id,
            "world": world,
            "changed_fields": changed_fields
        }

    # --------------------------------------------------------
    # 1. OS
    # --------------------------------------------------------

    os_info = world.get("os", {})

    if (
        os_info.get("name", "").lower() == "ubuntu"
        and os_info.get("version") == "22.04"
    ):
        change_version(
            os_info,
            "version",
            "20.04",
            changed_fields,
            "os.version"
        )

    # --------------------------------------------------------
    # 2. Web Server
    # --------------------------------------------------------

    web_server = world.get("web_server", {})

    if (
        web_server.get("name", "").lower() == "apache"
        and web_server.get("version") == "2.4.51"
    ):
        change_version(
            web_server,
            "version",
            "2.4.49",
            changed_fields,
            "web_server.version"
        )

    # --------------------------------------------------------
    # 3. Framework / Runtime
    # --------------------------------------------------------

    framework = world.get("framework", {})

    if (
        framework.get("name", "").lower() == "php"
        and framework.get("version") == "8.1"
    ):
        change_version(
            framework,
            "version",
            "7.4",
            changed_fields,
            "framework.version"
        )

    # --------------------------------------------------------
    # 4. Services consistency
    # --------------------------------------------------------

    web_server_name = web_server.get("name")

    if web_server_name:
        for service in world.get("services", []):
            if service.get("protocol") in ("http", "https"):
                service["service"] = web_server_name

    return {
        "fake_world_id": f"fake-{environment_id}-mock",
        "source_environment_id": environment_id,
        "world": world,
        "changed_fields": changed_fields
    }


# ============================================================
# Project .env loader
# ============================================================

def load_project_env(override: bool = True) -> Path:
    """
    自动读取项目根目录下的 .env 文件。

    支持以下格式：

        KEY=value
        KEY="value"
        KEY='value'
        export KEY=value

    override=True 时：
        .env 中的值覆盖当前 shell 中同名的旧环境变量。

    因此修改并保存 .env 后，
    不再需要手动执行 source .env。
    """

    env_path = (
        Path(__file__)
        .resolve()
        .parent
        .parent
        / ".env"
    )

    if not env_path.exists():
        return env_path

    lines = env_path.read_text(
        encoding="utf-8"
    ).splitlines()

    for raw_line in lines:

        line = raw_line.strip()

        # 跳过空行和注释
        if not line or line.startswith("#"):
            continue

        # 兼容：
        # export KEY=value
        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip()

        if not key:
            continue

        # 去除最外层单引号或双引号
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value

    return env_path


# ============================================================
# API configuration
# ============================================================

def get_api_config():
    """
    自动读取项目根目录 .env，并返回 API 配置。

    .env 中的值优先于当前 shell 中已有的同名变量。
    因此修改并保存 .env 后，无需再执行 source .env。
    """

    env_path = load_project_env(
        override=True
    )

    api_key = os.getenv("MODEL_API_KEY")
    base_url = os.getenv("MODEL_BASE_URL")
    model_name = os.getenv("MODEL_NAME")

    missing = []

    if not api_key:
        missing.append("MODEL_API_KEY")

    if not base_url:
        missing.append("MODEL_BASE_URL")

    if not model_name:
        missing.append("MODEL_NAME")

    if missing:

        if not env_path.exists():
            extra_message = (
                f"\n.env file not found: {env_path}"
            )
        else:
            extra_message = (
                f"\n.env loaded from: {env_path}"
            )

        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
            + extra_message
        )

    return api_key, base_url, model_name


def build_chat_completions_url(base_url: str) -> str:
    """
    同时兼容：

    https://example.com

    和：

    https://example.com/v1
    """

    base_url = base_url.rstrip("/")

    if base_url.endswith("/v1"):
        return base_url + "/chat/completions"

    return base_url + "/v1/chat/completions"


# ============================================================
# Model output parsing
# ============================================================

def parse_model_json(content: str) -> dict:
    """
    将模型返回内容解析为 Python dict。

    理想情况：
        模型只返回合法 JSON。

    同时容忍：
        ```json
        {...}
        ```

    或模型在 JSON 前后额外输出少量文本。
    """

    text = content.strip()

    # --------------------------------------------------------
    # 情况 1：模型返回 Markdown code fence
    # --------------------------------------------------------

    if text.startswith("```"):

        lines = text.splitlines()

        if lines:
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    # --------------------------------------------------------
    # 情况 2：直接就是合法 JSON
    # --------------------------------------------------------

    try:
        return json.loads(text)

    except json.JSONDecodeError:
        pass

    # --------------------------------------------------------
    # 情况 3：JSON 前后还有其他文字
    # --------------------------------------------------------

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:

        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)

        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        "Model output is not valid JSON.\n\n"
        "Raw model content:\n"
        + content
    )


# ============================================================
# changed_fields normalization
# ============================================================

def normalize_changed_fields(fake_world: dict) -> dict:
    """
    统一 changed_fields 的路径格式。

    规范：

        changed_fields 中所有路径，
        都必须表示 world 对象内部的相对路径。

    正确：
        web_server.version
        os.version
        framework.version

    不规范：
        world.web_server.version
        world.os.version

    如果模型错误地加入了 world. 前缀，
    程序会自动删除。
    """

    changed_fields = fake_world.get(
        "changed_fields",
        []
    )

    if not isinstance(changed_fields, list):
        return fake_world

    normalized = []

    for field in changed_fields:

        if not isinstance(field, str):
            continue

        field = field.strip()

        # 自动删除 world. 前缀
        while field.startswith("world."):
            field = field[len("world."):]

        # 忽略空字符串
        if not field:
            continue

        # 去重，同时保持原始顺序
        if field not in normalized:
            normalized.append(field)

    fake_world["changed_fields"] = normalized

    return fake_world


# ============================================================
# API call
# ============================================================

def call_model_api(
    prompt: str,
    real_world: dict,
    manipulation_instruction: str = None
) -> dict:
    """
    调用 OpenAI-compatible Chat Completions API。
    """

    api_key, base_url, model_name = get_api_config()

    url = build_chat_completions_url(
        base_url
    )

    user_message = (
        "以下是本次实验的真实环境信息。\n"
        "请严格按照系统要求生成对应的 Fake World。\n\n"
        "REAL ENVIRONMENT:\n"
        + json.dumps(
            real_world,
            indent=2,
            ensure_ascii=False
        )
    )

    # --------------------------------------------------------
    # Controlled Manipulation Instruction
    # --------------------------------------------------------

    if manipulation_instruction:

        user_message += (
            "\n\n"
            + manipulation_instruction
        )

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": prompt
            },
            {
                "role": "user",
                "content": user_message
            }
        ],

        # 目前先使用较低温度，
        # 减少格式漂移和无意义随机修改。
        "temperature": 0.2
    }

    request_data = json.dumps(
        payload,
        ensure_ascii=False
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=request_data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )

    print(
        f"Calling model: {model_name}"
    )

    print(
        f"Endpoint: {url}"
    )

    print()

    try:

        with urllib.request.urlopen(
            request,
            timeout=120
        ) as response:

            raw_body = (
                response
                .read()
                .decode("utf-8")
            )

    except urllib.error.HTTPError as e:

        error_body = (
            e.read()
            .decode(
                "utf-8",
                errors="replace"
            )
        )

        raise RuntimeError(
            f"API HTTP error {e.code}:\n"
            f"{error_body}"
        )

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"API connection failed: {e}"
        )

    # --------------------------------------------------------
    # 解析 API 返回的大 JSON
    # --------------------------------------------------------

    try:

        response_data = json.loads(
            raw_body
        )

    except json.JSONDecodeError as e:

        raise RuntimeError(
            "API response itself is not valid JSON."
        ) from e

    # --------------------------------------------------------
    # 保存完整 API Response
    # --------------------------------------------------------

    save_json(
        response_data,
        RAW_RESPONSE_PATH
    )

    # --------------------------------------------------------
    # 提取模型真正生成的 content
    # --------------------------------------------------------

    try:

        content = (
            response_data["choices"][0]
            ["message"]["content"]
        )

    except (
        KeyError,
        IndexError,
        TypeError
    ) as e:

        raise RuntimeError(
            "Unexpected API response format. "
            f"Full response saved to "
            f"{RAW_RESPONSE_PATH}"
        ) from e

    return parse_model_json(
        content
    )


# ============================================================
# Basic schema validation
# ============================================================

def validate_basic_schema(
    fake_world: dict,
    real_world: dict
) -> None:
    """
    对模型返回 JSON 做第一层结构检查。

    注意：
    这里只检查基本 Schema。

    更复杂的：
        Real/Fake 差异检查
        技术一致性检查
        changed_fields 检查

    由 validator.py 负责。
    """

    required_top_level = [
        "fake_world_id",
        "source_environment_id",
        "world",
        "changed_fields"
    ]

    for field in required_top_level:

        if field not in fake_world:

            raise RuntimeError(
                f"Missing required field: {field}"
            )

    if not isinstance(
        fake_world["world"],
        dict
    ):

        raise RuntimeError(
            "'world' must be a JSON object."
        )

    if not isinstance(
        fake_world["changed_fields"],
        list
    ):

        raise RuntimeError(
            "'changed_fields' must be a list."
        )

    real_environment_id = real_world.get(
        "environment_id"
    )

    fake_source_id = fake_world.get(
        "source_environment_id"
    )

    if (
        real_environment_id
        and fake_source_id != real_environment_id
    ):

        raise RuntimeError(
            "source_environment_id does not match "
            "the real environment_id."
        )


# ============================================================
# Unified generation entry
# ============================================================

def generate_fake_world(
    real_world: dict,
    prompt: str,
    mode: str,
    manipulation_instruction: str = None,
    manipulation_id: str = None,
    target_field: str = None
) -> dict:
    """
    Fake World Generator 统一入口。
    """

    if mode == "mock":

        return build_mock_fake_world(
            real_world,
            manipulation_id=manipulation_id,
            target_field=target_field
        )

    if mode == "api":

        return call_model_api(
            prompt=prompt,
            real_world=real_world,
            manipulation_instruction=manipulation_instruction
        )

    raise ValueError(
        f"Unsupported generation mode: {mode}"
    )


# ============================================================
# Main
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Fake World Generator"
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

    args = parser.parse_args()

    print(
        "=== Fake World Generator ==="
    )

    print(
        f"Language: {args.lang}"
    )

    print(
        f"Mode: {args.mode}"
    )

    print()

    # --------------------------------------------------------
    # 1. Load real environment
    # --------------------------------------------------------

    real_world = load_json(
        REAL_WORLD_PATH
    )

    print(
        "Loaded real environment: "
        f"{real_world.get('environment_id')}"
    )

    # --------------------------------------------------------
    # 2. Load prompt
    # --------------------------------------------------------

    prompt = load_prompt(
        args.lang
    )

    print(
        "Loaded prompt: "
        f"{PROMPT_PATHS[args.lang]}"
    )

    print()

    # --------------------------------------------------------
    # 3. Generate Fake World
    # --------------------------------------------------------

    fake_world = generate_fake_world(
        real_world=real_world,
        prompt=prompt,
        mode=args.mode
    )

    # --------------------------------------------------------
    # 4. Normalize changed_fields
    # --------------------------------------------------------

    fake_world = normalize_changed_fields(
        fake_world
    )

    # --------------------------------------------------------
    # 5. Basic schema validation
    # --------------------------------------------------------

    validate_basic_schema(
        fake_world=fake_world,
        real_world=real_world
    )

    # --------------------------------------------------------
    # 6. Save Fake World
    # --------------------------------------------------------

    save_json(
        fake_world,
        FAKE_WORLD_PATH
    )

    print(
        "=== Generation Complete ==="
    )

    print(
        f"Saved to: {FAKE_WORLD_PATH}"
    )

    print()

    print(
        "Changed fields:"
    )

    changed_fields = fake_world.get(
        "changed_fields",
        []
    )

    if changed_fields:

        for field in changed_fields:
            print(
                f"- {field}"
            )

    else:

        print(
            "- None"
        )


if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print()

        print(
            "=== Generator Error ==="
        )

        print(
            str(e)
        )

        sys.exit(1)