#!/usr/bin/env python3
"""Render a Fake World into a self-contained observation bundle.

The generator and scenario builder intentionally keep the synthetic world
independent from a target container.  This module is the corresponding
artifact renderer: it turns ``scenario.json``/``fake_world.json`` into files
which a later injector can copy into an observation root.  It does not call
Docker and has no project-specific runtime dependencies.

Typical use::

    python3 src/observation_bundle.py \
        --scenario runs/XBEN-028-24/generated/scenario-.../scenario.json \
        --output outputs/aoi-observations

The output directory contains ``index.json``, ``environment.json``,
``security_assessment.json``, claim-category JSON files, generated HTML pages,
and HTTP header/Nmap text files.  All paths in ``index.json`` are relative to
the output directory and include SHA-256 checksums.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


SCHEMA_VERSION = "aoi.observation-bundle/v1"
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
DEFAULT_CLAIM_CATEGORIES = (
    "vulnerabilities",
    "wrong_patch_status",
    "assets",
    "credentials",
    "permissions",
    "objectives",
    "attack_paths",
    "policy",
)


def load_json(path: Path | str) -> dict[str, Any]:
    """Load a UTF-8 JSON object with a useful error for callers."""

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"JSON 文件不存在: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 文件格式错误: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象: {source}")
    return value


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_text(value), encoding="utf-8")


def _json_text(value: Any) -> str:
    """Serialize JSON in the same stable form used by files on disk."""

    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _text_dump(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_list(value: Any) -> list[Any]:
    """Represent either a list claim or a singleton claim consistently."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _line_value(value: Any, default: str = "unknown") -> str:
    """Return one printable line for header and scanner-style observations."""

    text = default if value is None else str(value)
    return CONTROL_CHARACTERS.sub(" ", text).strip() or default


def _public_value(value: Any) -> Any:
    """Remove generator-only markers before values become target observations."""

    if isinstance(value, Mapping):
        return {
            str(key): _public_value(item)
            for key, item in value.items()
            if key not in {"profile_id", "synthetic"}
        }
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return copy.deepcopy(value)


def _source_world(fake_world: Mapping[str, Any]) -> dict[str, Any]:
    """Return the world object for both wrapped and legacy input formats."""

    # Older fixtures and generate_scenario.py may store the Real World fields
    # at the top level while putting only modified fields under ``world``.
    # Start with that legacy view, then let the wrapped view override it.
    fields = (
        "os",
        "web_server",
        "framework",
        "database",
        "services",
        "vulnerabilities",
    )
    result = {
        key: copy.deepcopy(fake_world[key])
        for key in fields
        if key in fake_world
    }
    world = fake_world.get("world")
    if isinstance(world, Mapping):
        for key, value in world.items():
            if (
                isinstance(value, Mapping)
                and isinstance(result.get(key), Mapping)
            ):
                merged = copy.deepcopy(dict(result[key]))
                merged.update(copy.deepcopy(dict(value)))
                result[key] = merged
            else:
                result[key] = copy.deepcopy(value)
    return result


def _claims(fake_world: Mapping[str, Any]) -> dict[str, Any]:
    value = fake_world.get("claims", {})
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _claim_target_ref(target: str) -> tuple[Optional[int], Optional[str]]:
    """Resolve numeric and ID-addressed vulnerability target paths."""

    text = str(target)
    numeric = re.search(r"vulnerabilities\.(\d+)\.(?:status|cve)$", text)
    if numeric:
        return int(numeric.group(1)), None
    addressed = re.search(r"vulnerabilities\[([^\]]+)\]\.(?:status|cve)$", text)
    if addressed:
        value = addressed.group(1).strip("\"'")
        if value.isdecimal():
            return int(value), None
        return None, value
    return None, None


def _apply_vulnerability_claims(
    vulnerabilities: Iterable[Any],
    claims: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Merge fake CVE/vulnerability/status claims into the API view.

    ``generate_scenario.py`` intentionally records claims separately.  For
    the observation API, those claims must become visible alongside the real
    vulnerability list.  Existing IDs are updated instead of duplicated;
    claims without an ID are appended as synthetic entries.
    """

    rendered = [copy.deepcopy(item) for item in vulnerabilities if isinstance(item, Mapping)]
    by_id = {
        str(item.get("id")): index
        for index, item in enumerate(rendered)
        if item.get("id") is not None
    }

    vulnerability_claims = _as_list(claims.get("vulnerabilities"))
    for claim in vulnerability_claims:
        if not isinstance(claim, Mapping):
            continue
        item = {key: copy.deepcopy(value) for key, value in claim.items() if key not in {"profile_id", "synthetic"}}
        claim_id = item.get("id") or item.get("vulnerability_id")
        target = item.get("target") or item.get("target_path") or item.get("path")
        _, target_id = _claim_target_ref(str(target)) if target else (None, None)
        claim_id = claim_id or target_id
        # Accept the descriptive key often used by the Qwen proposal format.
        if "cve" not in item and "fake_cve" in item:
            item["cve"] = item.pop("fake_cve")
        if claim_id is not None and str(claim_id) in by_id:
            rendered[by_id[str(claim_id)]].update(item)
        else:
            if claim_id is not None:
                by_id[str(claim_id)] = len(rendered)
            rendered.append(item)

    for claim in _as_list(claims.get("wrong_patch_status")):
        if not isinstance(claim, Mapping):
            continue
        target = claim.get("target") or claim.get("target_path") or claim.get("path")
        index, target_id = _claim_target_ref(str(target)) if target else (None, None)
        claim_id = claim.get("id") or claim.get("vulnerability_id")
        if claim_id is None:
            claim_id = target_id
        if claim_id is not None and str(claim_id) in by_id:
            index = by_id[str(claim_id)]
        if index is None or index < 0 or index >= len(rendered):
            continue
        fake_status = (
            claim.get("status")
            or claim.get("new_status")
            or claim.get("fake_value")
            or claim.get("value")
            or "patched"
        )
        rendered[index]["status"] = fake_status

    return rendered


def render_environment(fake_world: Mapping[str, Any]) -> dict[str, Any]:
    """Render environment metadata without exposing generator internals."""

    world = _source_world(fake_world)
    return {
        "schema_version": "aoi.environment-observation/v1",
        "environment_id": fake_world.get("source_environment_id") or fake_world.get("environment_id"),
        "world": world,
    }


def render_security_assessment(fake_world: Mapping[str, Any]) -> dict[str, Any]:
    world = _source_world(fake_world)
    claims = _claims(fake_world)
    return {
        "schema_version": "aoi.security-assessment-observation/v1",
        # Keep the shape produced by vulnerability_renderer.py and consumed
        # by the existing attack runner (jq .security_assessment.vulnerabilities).
        "security_assessment": {
            "vulnerabilities": _apply_vulnerability_claims(
                world.get("vulnerabilities", []),
                claims,
            ),
        },
    }


def render_headers(world: Mapping[str, Any]) -> str:
    server_value = world.get("web_server")
    framework_value = world.get("framework")
    server = server_value if isinstance(server_value, Mapping) else {}
    framework = framework_value if isinstance(framework_value, Mapping) else {}
    server_name = _line_value(server.get("name"))
    server_version = _line_value(server.get("version"))
    framework_name = _line_value(framework.get("name"))
    framework_version = _line_value(framework.get("version"))
    return (
        f"Server: {server_name}/{server_version}\n"
        f"X-Powered-By: {framework_name}/{framework_version}"
    )


def render_nmap(world: Mapping[str, Any]) -> str:
    server_value = world.get("web_server")
    server = server_value if isinstance(server_value, Mapping) else {}
    name = _line_value(server.get("name"))
    version = _line_value(server.get("version"))
    lines: list[str] = []
    services = world.get("services", [])
    if not isinstance(services, list):
        return ""
    for service in services:
        if not isinstance(service, Mapping):
            continue
        if service.get("port") is None:
            continue
        port = _line_value(service.get("port"))
        protocol = _line_value(service.get("protocol"), "tcp").lower()
        if protocol == "http":
            lines.append(f"{port}/tcp open  http     {name} httpd {version}")
        elif protocol == "https":
            lines.append(f"{port}/tcp open  ssl/http {name} httpd {version}")
        else:
            service_name = _line_value(service.get("service"), protocol)
            lines.append(f"{port}/tcp open  {service_name} {name} {version}")
    return "\n".join(lines)


def _environment_page(environment: Mapping[str, Any]) -> str:
    world = environment.get("world") or {}
    rows: list[str] = []
    for key in ("os", "web_server", "framework", "database"):
        value = world.get(key)
        if isinstance(value, Mapping):
            label = html.escape(_line_value(key))
            name = html.escape(_line_value(value.get("name")))
            version = html.escape(_line_value(value.get("version")))
            rows.append(f"<tr><th>{label}</th><td>{name} {version}</td></tr>")
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>Environment</title></head>"
        "<body><h1>Environment</h1><table>" + "".join(rows) + "</table></body></html>"
    )


def _claim_page(title: str, claims: Any) -> str:
    payload = html.escape(json.dumps(claims, ensure_ascii=False, indent=2))
    title = _line_value(title, "Claims")
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"><title>"
        + html.escape(title)
        + "</title></head><body><h1>"
        + html.escape(title)
        + "</h1><pre>"
        + payload
        + "</pre></body></html>"
    )


def _observation_page(value: Mapping[str, Any], fallback: str) -> tuple[str, str]:
    """Get a safe relative page path and HTML body from a generated claim."""

    raw_path = value.get("path") or value.get("url") or value.get("name") or fallback
    raw_path = str(raw_path).lstrip("/")
    # A page can never escape the bundle's pages directory.
    safe_parts = [part for part in Path(raw_path).parts if part not in {"", ".", ".."}]
    path = "/".join(safe_parts) or fallback
    if not path.lower().endswith((".html", ".htm")):
        path += ".html"
    body = value.get("html")
    if isinstance(body, str) and body.strip():
        # ``html`` is the explicit markup channel used by generated pages.
        body = body
    else:
        plain_body = value.get("content") or value.get("body")
        if plain_body is not None:
            body = "<p>" + html.escape(str(plain_body)) + "</p>"
        else:
            body = None
    if body is None:
        body = _claim_page(
            str(value.get("title") or "Synthetic page"),
            _public_value(value),
        )
    return path, str(body)


def _relative_file(path: Path, output_dir: Path, kind: str, source: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "kind": kind,
        "format": "json" if path.suffix == ".json" else "html" if path.suffix in {".html", ".htm"} else "text",
        "source": source,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _safe_page_path(raw_path: Any, fallback: str, used: set[str]) -> str:
    """Normalize a generated page path and make collisions deterministic."""

    raw = str(raw_path or fallback).lstrip("/")
    parts = [
        _safe_component(part, "page")
        for part in Path(raw).parts
        if part not in {"", ".", ".."}
    ]
    path = "/".join(parts) or fallback
    if not path.lower().endswith((".html", ".htm")):
        path += ".html"

    candidate = path
    stem = Path(path).stem
    suffix = Path(path).suffix or ".html"
    parent = Path(path).parent.as_posix()
    counter = 2
    used_keys = {item.casefold() for item in used}
    while candidate.casefold() in used_keys:
        name = f"{stem}-{counter}{suffix}"
        candidate = f"{parent}/{name}" if parent != "." else name
        counter += 1
    used.add(candidate)
    return candidate


def _safe_component(value: Any, fallback: str = "claim") -> str:
    """Return a path-safe single filename component."""

    component = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip(".")
    return component or fallback


def _claim_file_names(categories: Iterable[str]) -> dict[str, str]:
    """Assign collision-free, stable file components to claim categories."""

    names: dict[str, str] = {}
    used: set[str] = set()
    for category in sorted(categories):
        base = _safe_component(category)
        candidate = base
        counter = 2
        while candidate.casefold() in used:
            candidate = f"{base}-{counter}"
            counter += 1
        used.add(candidate.casefold())
        names[category] = candidate
    return names


def _artifact_descriptor(relative_path: str) -> tuple[str, str]:
    """Map a relative artifact path to manifest kind and source labels."""

    if relative_path == "environment.json":
        return "environment", "world"
    if relative_path == "security_assessment.json":
        return "security_assessment", "world+claims.vulnerabilities"
    if relative_path == "headers/http_headers.txt":
        return "http_headers", "world"
    if relative_path == "headers/nmap.txt":
        return "nmap", "world"
    if relative_path.startswith("claims/"):
        return "claim", f"claims.{Path(relative_path).stem}"
    if relative_path == "pages/environment.html":
        return "page", "world"
    if relative_path.startswith("pages/claims-"):
        category = Path(relative_path).stem.removeprefix("claims-")
        return "page", f"claims.{category}"
    if relative_path.startswith("pages/"):
        return "page", "observations[]"
    return "observation", "generated"


def render_artifacts(
    fake_world: Mapping[str, Any],
    scenario: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    """Return all injectable files as ``relative_path -> UTF-8 text``.

    This pure renderer is intentionally independent of the filesystem.  The
    optional scenario is accepted for callers that pass both input artifacts;
    host-side callers can use it to associate the returned bundle with a run.
    The returned paths are stable and always relative to an ``aoi-observations``
    root, making the mapping directly consumable by a future injector.
    """

    del scenario  # Reserved for future per-scenario observation channels.
    world = _source_world(fake_world)
    claims = _claims(fake_world)
    claim_names = _claim_file_names(claims)
    environment = render_environment(fake_world)
    artifacts: dict[str, str] = {
        "environment.json": _json_text(environment),
        "security_assessment.json": _json_text(render_security_assessment(fake_world)),
        "headers/http_headers.txt": render_headers(world).rstrip() + "\n",
        "headers/nmap.txt": render_nmap(world).rstrip() + "\n",
        "pages/environment.html": _environment_page(environment).rstrip() + "\n",
    }

    for category in sorted(claims):
        claim_path = f"claims/{claim_names[category]}.json"
        artifacts[claim_path] = _json_text(
            {
                "schema_version": "aoi.claim-observation/v1",
                "category": category,
                "claims": _public_value(claims[category]),
            }
        )

    used_pages = {
        "environment.html",
        *(f"claims-{name}.html" for name in claim_names.values()),
    }
    for index, item in enumerate(_as_list(fake_world.get("observations"))):
        if not isinstance(item, Mapping):
            continue
        fallback = f"synthetic-{index + 1}.html"
        raw_path, body = _observation_page(item, fallback)
        page_path = _safe_page_path(raw_path, fallback, used_pages)
        artifacts[f"pages/{page_path}"] = body.rstrip() + "\n"

    for category in sorted(claims):
        page_path = f"pages/claims-{claim_names[category]}.html"
        artifacts[page_path] = _claim_page(
            f"Claims: {category}",
            _public_value(claims[category]),
        ).rstrip() + "\n"

    return artifacts


def build_bundle(
    fake_world: Mapping[str, Any],
    output_dir: Path | str,
    *,
    scenario: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Write observation files and return the index manifest.

    ``output_dir`` is the directory that will be copied to the target
    observation root (normally named ``aoi-observations``).  Existing files in
    that directory are replaced only when they have one of the generated
    names; unrelated files are left untouched.
    """

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = render_artifacts(fake_world, scenario=scenario)
    files: list[dict[str, Any]] = []
    for relative_path, content in artifacts.items():
        destination = output / relative_path
        _text_dump(destination, content)
        kind, source = _artifact_descriptor(relative_path)
        files.append(_relative_file(destination, output, kind, source))

    # ``index.json`` is itself exposed to the target.  Keep only public bundle
    # metadata; selection/profile/change details stay in the host-side run.
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": "/aoi-observations",
        "files": files,
    }
    _json_dump(output / "index.json", manifest)
    return manifest


def _resolve_inputs(scenario_path: Optional[Path], fake_world_path: Optional[Path]) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    scenario: Optional[dict[str, Any]] = None
    if scenario_path:
        scenario = load_json(scenario_path)
        if fake_world_path is None:
            artifacts = scenario.get("artifacts")
            name = artifacts.get("fake_world") if isinstance(artifacts, Mapping) else None
            if name:
                fake_world_path = scenario_path.parent / str(name)
            elif isinstance(scenario.get("fake_world"), Mapping):
                return copy.deepcopy(dict(scenario["fake_world"])), scenario
    if fake_world_path is None:
        raise ValueError("请提供 --fake-world，或提供包含 artifacts.fake_world 的 --scenario")
    return load_json(fake_world_path), scenario


def self_test() -> None:
    """Run a no-network smoke test for CI and local verification."""

    sample = {
        "fake_world_id": "fake-self-test",
        "source_environment_id": "self-test",
        "selected_profiles": ["fake_cve", "fake_page"],
        "changed_fields": ["claims.vulnerabilities[]"],
        "world": {
            "os": {"name": "Debian", "version": "12"},
            "web_server": {"name": "Apache", "version": "2.4.54"},
            "framework": {"name": "PHP", "version": "8.2"},
            "services": [{"port": 80, "protocol": "http", "service": "Apache"}],
            "vulnerabilities": [{"id": "v-1", "type": "LFI", "status": "present"}],
        },
        "claims": {
            "vulnerabilities": [{"id": "v-1", "cve": "CVE-2099-0001", "synthetic": True}],
            "policy": {"authorization": "synthetic"},
        },
        "observations": [{"title": "Admin", "path": "/admin", "content": "<h1>Admin</h1>"}],
    }
    with tempfile.TemporaryDirectory() as directory:
        manifest = build_bundle(sample, Path(directory) / "aoi-observations")
        root = Path(directory) / "aoi-observations"
        assert (root / "index.json").exists()
        assessment = json.loads((root / "security_assessment.json").read_text(encoding="utf-8"))
        assert assessment["security_assessment"]["vulnerabilities"][0]["cve"] == "CVE-2099-0001"
        assert "synthetic" not in json.dumps(assessment, ensure_ascii=False)
        assert any(item["path"] == "pages/admin.html" for item in manifest["files"])
        assert any(item["path"] == "claims/policy.json" for item in manifest["files"])
    print("observation_bundle self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Fake World observation bundle")
    parser.add_argument("--scenario", type=Path, help="scenario.json; resolves artifacts.fake_world")
    parser.add_argument("--fake-world", type=Path, help="fake_world.json")
    parser.add_argument("--output", type=Path, default=Path("outputs/aoi-observations"), help="bundle output directory")
    parser.add_argument("--self-test", action="store_true", help="run local smoke test")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    fake_world, scenario = _resolve_inputs(args.scenario, args.fake_world)
    manifest = build_bundle(fake_world, args.output, scenario=scenario)
    print(Path(args.output).resolve())
    print(f"generated {len(manifest['files'])} observation files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
