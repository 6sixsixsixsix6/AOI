import html as html_lib
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _text(value, default="unknown") -> str:
    """Return a stable display value without allowing control characters."""

    if value is None:
        value = default
    return str(value)


def _header_value(value, default="unknown") -> str:
    """Keep generated header values on one line and free of control bytes."""

    text = _text(value, default)
    return re.sub(r"[\x00-\x1f\x7f]", " ", text).strip() or default


def _html_value(value, default="unknown") -> str:
    return html_lib.escape(_header_value(value, default), quote=True)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}

def load_json(path: str):
    """
    读取 JSON 文件。
    """
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def render_html(world: dict) -> str:
    """
    根据统一 Fake World 生成 HTML 风格的 Observation。
    """
    world = _mapping(world)
    web_server = _mapping(world.get("web_server"))
    framework = _mapping(world.get("framework"))

    server_name = _html_value(web_server.get("name"))
    server_version = _html_value(web_server.get("version"))

    framework_name = _html_value(framework.get("name"))
    framework_version = _html_value(framework.get("version"))

    html = f"""<!-- generated server metadata -->
<meta name="server" content="{server_name}/{server_version}">
<meta name="runtime" content="{framework_name}/{framework_version}">
"""

    return html


def render_http_header(world: dict) -> str:
    """
    根据统一 Fake World 生成 HTTP Header 风格的 Observation。
    """
    world = _mapping(world)
    web_server = _mapping(world.get("web_server"))
    framework = _mapping(world.get("framework"))

    server_name = _header_value(web_server.get("name"))
    server_version = _header_value(web_server.get("version"))

    framework_name = _header_value(framework.get("name"))
    framework_version = _header_value(framework.get("version"))

    header = (
        f"Server: {server_name}/{server_version}\n"
        f"X-Powered-By: {framework_name}/{framework_version}"
    )

    return header


def render_nmap(world: dict) -> str:
    """
    根据统一 Fake World 生成 Nmap 风格的 Observation。
    """
    world = _mapping(world)
    web_server = _mapping(world.get("web_server"))

    server_name = _header_value(web_server.get("name"))
    server_version = _header_value(web_server.get("version"))

    services = world.get("services", [])
    if not isinstance(services, list):
        return ""

    lines = []

    for service in services:
        if not isinstance(service, dict):
            continue
        port = _header_value(service.get("port"))
        protocol = _header_value(service.get("protocol"), "tcp").lower()

        if protocol == "http":
            lines.append(
                f"{port}/tcp open  http  "
                f"{server_name} httpd {server_version}"
            )

        elif protocol == "https":
            lines.append(
                f"{port}/tcp open  ssl/http  "
                f"{server_name} httpd {server_version}"
            )

    return "\n".join(lines)


def save_outputs(
    html: str,
    header: str,
    nmap: str,
    fake_world: dict
):
    """
    将不同 Observation Source 的渲染结果保存到 outputs 目录。
    """

    output_dir = PROJECT_ROOT / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 单独保存不同 Observation Source
    # rstrip() 去掉末尾已有空白，再统一补一个换行
    (output_dir / "html.txt").write_text(
        html.rstrip() + "\n",
        encoding="utf-8"
    )

    (output_dir / "http_headers.txt").write_text(
        header.rstrip() + "\n",
        encoding="utf-8"
    )

    (output_dir / "nmap.txt").write_text(
        nmap.rstrip() + "\n",
        encoding="utf-8"
    )

    # 保存统一结构，便于以后交给自动插入模块
    rendered = {
        "fake_world_id": fake_world.get("fake_world_id"),
        "source_environment_id": fake_world.get(
            "source_environment_id"
        ),
        "observations": {
            "html": html.rstrip(),
            "http_headers": header.rstrip(),
            "nmap": nmap.rstrip()
        }
    }

    with (output_dir / "rendered_observations.json").open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            rendered,
            f,
            indent=2,
            ensure_ascii=False
        )


def main():
    """
    Renderer 主流程。
    """

    fake_world = load_json(
        str(PROJECT_ROOT / "configs/fake_world.json")
    )

    world = fake_world["world"]

    html = render_html(world)
    header = render_http_header(world)
    nmap = render_nmap(world)

    print("=== HTML ===")
    print(html)

    print("=== HTTP Header ===")
    print(header)
    print()

    print("=== Nmap ===")
    print(nmap)

    save_outputs(
        html=html,
        header=header,
        nmap=nmap,
        fake_world=fake_world
    )

    print()
    print("=== Saved ===")
    print(
        "Rendered observations saved to outputs/"
    )


if __name__ == "__main__":
    main()
