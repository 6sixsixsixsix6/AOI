#!/usr/bin/env python3
"""Apply, verify, and restore one AOI observation bundle in Docker.

The injector only writes generated observation artifacts.  It records every
target path, the pre-existing file (when present), and both local/remote
checksums.  A failed apply is rolled back immediately; a later ``restore``
returns the container to the exact pre-apply state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observation_bundle import build_bundle


REMOTE_ROOT = "/var/www/html"
OBSERVATION_ROOT = f"{REMOTE_ROOT}/aoi-observations"
ASSESSMENT_PATH = f"{REMOTE_ROOT}/security_assessment.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _docker(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], text=True, capture_output=True)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"docker {' '.join(args)} 失败 ({result.returncode}): {detail}")
    return result


def _exists(container: str, remote: str) -> bool:
    result = _docker(["exec", container, "sh", "-c", "test -e \"$1\"", "sh", remote], check=False)
    return result.returncode == 0


def _is_dir(container: str, remote: str) -> bool:
    result = _docker(["exec", container, "sh", "-c", "test -d \"$1\"", "sh", remote], check=False)
    return result.returncode == 0


def _remote_sha256(container: str, remote: str) -> str | None:
    result = _docker(["exec", container, "sha256sum", remote], check=False)
    return result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else None


def _copy_to(container: str, source: Path, remote: str) -> None:
    parent = posixpath.dirname(remote)
    _docker(["exec", container, "mkdir", "-p", parent])
    _docker(["cp", str(source), f"{container}:{remote}"])


def _remove(container: str, remote: str) -> None:
    _docker(["exec", container, "rm", "-rf", remote])


def _scenario_files(scenario_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario_path = scenario_dir / "scenario.json"
    world_path = scenario_dir / "fake_world.json"
    if not scenario_path.is_file() or not world_path.is_file():
        raise RuntimeError("场景目录必须同时包含 scenario.json 和 fake_world.json")
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        fake_world = json.loads(world_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"场景 JSON 格式错误: {exc}") from exc
    if not isinstance(scenario, dict) or not isinstance(fake_world, dict):
        raise RuntimeError("场景 JSON 根节点必须是对象")
    return scenario, fake_world


def _target_for(relative: str) -> str:
    relative = relative.replace("\\", "/").lstrip("/")
    parts = [part for part in relative.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        raise RuntimeError(f"不允许的注入路径: {relative}")
    normalized = "/".join(parts)
    if normalized == "security_assessment.json":
        return ASSESSMENT_PATH
    if normalized == "environment.json" or normalized.startswith("claims/") or normalized.startswith("pages/") or normalized.startswith("headers/") or normalized == "index.json":
        return f"{OBSERVATION_ROOT}/{normalized}"
    raise RuntimeError(f"不允许的注入文件: {relative}")


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _restore_records(manifest: dict[str, Any], container: str) -> None:
    for record in reversed(manifest.get("records", [])):
        target = record["target_path"]
        if record.get("existed_before"):
            backup = record.get("backup_path")
            if not backup or not Path(backup).is_file():
                raise RuntimeError(f"缺少备份文件，拒绝恢复: {target}")
            _copy_to(container, Path(backup), target)
        else:
            _remove(container, target)
    root_record = manifest.get("observation_root", {})
    if root_record.get("created_by_apply") and _is_dir(container, OBSERVATION_ROOT):
        # Only remove an empty generated root; preserve unexpected files for diagnosis.
        check = _docker(["exec", container, "sh", "-c", "test -z \"$(find \"$1\" -mindepth 1 -print -quit)\"", "sh", OBSERVATION_ROOT], check=False)
        if check.returncode == 0:
            _remove(container, OBSERVATION_ROOT)


def apply(scenario_dir: Path, container: str, run_dir: Path) -> Path:
    scenario, fake_world = _scenario_files(scenario_dir.resolve())
    staging = run_dir / "injection_staging"
    # Build into staging first so the public index and the files copied to the
    # target are generated from exactly the same content and hashes.
    build_bundle(fake_world, staging, scenario=scenario)
    artifacts = {
        path.relative_to(staging).as_posix(): path.read_text(encoding="utf-8")
        for path in staging.rglob("*")
        if path.is_file()
    }
    if not artifacts:
        raise RuntimeError("场景没有可注入的观测文件")
    backup_dir = run_dir / "injection_backups"
    staging.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    root_existed = _is_dir(container, OBSERVATION_ROOT)
    if root_existed:
        raise RuntimeError(f"目标观测目录已经存在，拒绝覆盖: {OBSERVATION_ROOT}")

    manifest_path = run_dir / "injection_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "aoi.injection-manifest/v2",
        "created_at": _now(),
        "container": container,
        "scenario_dir": str(scenario_dir.resolve()),
        "scenario_id": scenario.get("scenario_id"),
        "selected_profiles": scenario.get("selection", {}).get("selected_ids", []),
        "observation_root": {"path": OBSERVATION_ROOT, "existed_before": root_existed, "created_by_apply": not root_existed},
        "records": [],
        "status": "applying",
    }
    _write_manifest(manifest_path, manifest)
    try:
        for relative, content in sorted(artifacts.items()):
            target = _target_for(relative)
            local = staging / relative
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(content, encoding="utf-8")
            local_hash = _sha256(local)
            existed = _exists(container, target)
            original_hash = _remote_sha256(container, target) if existed else None
            backup = None
            if existed:
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                _docker(["cp", f"{container}:{target}", str(backup)])
            record = {"relative_path": relative, "target_path": target, "existed_before": existed, "backup_path": str(backup) if backup else None, "original_sha256": original_hash, "sha256": local_hash}
            manifest["records"].append(record)
            _write_manifest(manifest_path, manifest)
            _copy_to(container, local, target)
            remote_hash = _remote_sha256(container, target)
            if remote_hash != local_hash:
                raise RuntimeError(f"注入校验失败: {target}")
        manifest["status"] = "applied"
        manifest["applied_at"] = _now()
        _write_manifest(manifest_path, manifest)
        return manifest_path
    except Exception:
        manifest["status"] = "apply_failed_rolling_back"
        _write_manifest(manifest_path, manifest)
        try:
            _restore_records(manifest, container)
            manifest["status"] = "apply_failed_restored"
            manifest["restored_at"] = _now()
            _write_manifest(manifest_path, manifest)
        except Exception as rollback_error:
            manifest["rollback_error"] = str(rollback_error)
            _write_manifest(manifest_path, manifest)
        raise


def restore(manifest_path: Path, container: str | None = None) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    container = container or manifest.get("container")
    if not container:
        raise RuntimeError("恢复时缺少容器 ID")
    if manifest.get("status") == "restored":
        return
    _restore_records(manifest, container)
    for record in manifest.get("records", []):
        target = record["target_path"]
        if record.get("existed_before"):
            expected = record.get("original_sha256")
            actual = _remote_sha256(container, target)
            if expected and actual != expected:
                raise RuntimeError(f"恢复后校验不一致: {target}")
        elif _exists(container, target):
            raise RuntimeError(f"恢复后仍有注入文件: {target}")
    if manifest.get("observation_root", {}).get("created_by_apply") and _is_dir(container, OBSERVATION_ROOT):
        raise RuntimeError(f"恢复后观测目录仍存在: {OBSERVATION_ROOT}")
    manifest["restored_at"] = _now()
    manifest["status"] = "restored"
    _write_manifest(manifest_path, manifest)


def verify(manifest_path: Path, container: str | None = None) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    container = container or manifest.get("container")
    if not container:
        raise RuntimeError("校验时缺少容器 ID")
    restored = manifest.get("status") == "restored"
    for record in manifest.get("records", []):
        target = record["target_path"]
        if restored:
            if record.get("existed_before"):
                expected = record.get("original_sha256")
                if expected and _remote_sha256(container, target) != expected:
                    raise RuntimeError(f"恢复文件校验不一致: {target}")
            elif _exists(container, target):
                raise RuntimeError(f"仍存在注入文件: {target}")
        elif _remote_sha256(container, target) != record.get("sha256"):
            raise RuntimeError(f"注入文件校验不一致: {target}")
    if restored and manifest.get("observation_root", {}).get("created_by_apply") and _is_dir(container, OBSERVATION_ROOT):
        raise RuntimeError(f"恢复后观测目录仍存在: {OBSERVATION_ROOT}")


def main() -> int:
    parser = argparse.ArgumentParser(description="应用、校验或恢复 AOI 观测 Bundle")
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--scenario-dir", required=True, type=Path)
    apply_parser.add_argument("--container", required=True)
    apply_parser.add_argument("--run-dir", required=True, type=Path)
    for name in ("restore", "verify"):
        item = sub.add_parser(name)
        item.add_argument("--manifest", required=True, type=Path)
        item.add_argument("--container")
    args = parser.parse_args()
    try:
        if args.command == "apply":
            print(apply(args.scenario_dir, args.container, args.run_dir))
        elif args.command == "restore":
            restore(args.manifest, args.container)
            print("restored")
        else:
            verify(args.manifest, args.container)
            print("verified")
        return 0
    except Exception as exc:
        print(f"live injector error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
