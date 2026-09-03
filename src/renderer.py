import json
from pathlib import Path


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
    web_server = world["web_server"]
    framework = world["framework"]

    server_name = web_server["name"]
    server_version = web_server["version"]

    framework_name = framework["name"]
    framework_version = framework["version"]

    html = f"""<!-- generated server metadata -->
<meta name="server" content="{server_name}/{server_version}">
<meta name="runtime" content="{framework_name}/{framework_version}">
"""

    return html


def render_http_header(world: dict) -> str:
    """
    根据统一 Fake World 生成 HTTP Header 风格的 Observation。
    """
    web_server = world["web_server"]
    framework = world["framework"]

    server_name = web_server["name"]
    server_version = web_server["version"]

    framework_name = framework["name"]
    framework_version = framework["version"]

    header = (
        f"Server: {server_name}/{server_version}\n"
        f"X-Powered-By: {framework_name}/{framework_version}"
    )

    return header


def render_nmap(world: dict) -> str:
    """
    根据统一 Fake World 生成 Nmap 风格的 Observation。
    """
    web_server = world["web_server"]

    server_name = web_server["name"]
    server_version = web_server["version"]

    services = world.get("services", [])

    lines = []

    for service in services:
        port = service["port"]
        protocol = service["protocol"]

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

    output_dir = Path("outputs")
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
        "configs/fake_world.json"
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