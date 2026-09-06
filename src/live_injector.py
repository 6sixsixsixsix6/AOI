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
import posixpath
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from observation_bundle import build_bundle


REMOTE_ROOT = "/var/www/html"
OBSERVATION_ROOT = f"{REMOTE_ROOT}/aoi-observations"
ASSESSMENT_PATH = f"{REMOTE_ROOT}/security_assessment.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_SCHEMA_VERSION = "aoi.scenario/v2"
FAKE_WORLD_SCHEMA_VERSION = "aoi.fake-world/v2"
MANIFEST_SCHEMA_VERSION = "aoi.injection-manifest/v2"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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


def _assert_no_remote_symlink(container: str, remote: str) -> None:
    """Reject symlinks in a target path before any Docker file operation."""

    result = _docker(
        [
            "exec",
            container,
            "sh",
            "-eu",
            "-c",
            'path="$1"; '
            'while :; do '
            '  if [ -L "$path" ]; then printf "%s\\n" __AOI_SYMLINK__; exit 42; fi; '
            '  [ "$path" = "/" ] && break; '
            '  next=${path%/*}; [ -n "$next" ] || next=/; path="$next"; '
            'done; '
            'printf "%s\\n" __AOI_NO_SYMLINK__',
            "sh",
            remote,
        ],
        check=False,
    )
    if result.returncode == 42 and result.stdout.strip() == "__AOI_SYMLINK__":
        raise RuntimeError(f"远程路径包含不允许的符号链接: {remote}")
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"无法检查远程路径符号链接 {remote}: {detail or result.returncode}")
    if result.stdout.strip() != "__AOI_NO_SYMLINK__":
        raise RuntimeError(f"远程路径符号链接检查返回了无效结果: {remote}")


def _exists(container: str, remote: str) -> bool:
    # ``docker exec`` forwards the command exit code, so a plain ``test``
    # cannot distinguish "missing" (1) from a failed container invocation.
    # Emit an explicit marker and reserve a non-zero result for infrastructure
    # errors.
    _assert_no_remote_symlink(container, remote)
    result = _docker(
        [
            "exec",
            container,
            "sh",
            "-c",
            'if [ -L "$1" ]; then printf "%s\\n" __AOI_PRESENT__; '
            'else test -e "$1"; status=$?; '
            '  if [ "$status" -eq 0 ]; then printf "%s\\n" __AOI_PRESENT__; '
            '  elif [ "$status" -eq 1 ]; then printf "%s\\n" __AOI_ABSENT__; '
            '  else exit "$status"; fi; '
            'fi',
            "sh",
            remote,
        ],
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"无法检查远程路径 {remote}: {detail or result.returncode}")
    marker = result.stdout.strip()
    if marker == "__AOI_PRESENT__":
        return True
    if marker == "__AOI_ABSENT__":
        return False
    raise RuntimeError(f"远程路径检查返回了无效结果: {remote}")


def _remote_sha256(container: str, remote: str) -> str | None:
    # As with ``_exists``, a shell probe makes a missing file explicit instead
    # of treating every exit code 1 from Docker or sha256sum as absence.
    _assert_no_remote_symlink(container, remote)
    result = _docker(
        [
            "exec",
            container,
            "sh",
            "-c",
            'if [ -L "$1" ]; then printf "%s\\n" __AOI_SYMLINK__; exit 42; fi; '
            'test -e "$1"; status=$?; '
            'if [ "$status" -eq 1 ]; then printf "%s\\n" __AOI_ABSENT__; '
            'elif [ "$status" -eq 0 ]; then '
            '  if [ ! -f "$1" ]; then printf "%s\\n" __AOI_NOT_REGULAR__; exit 43; fi; '
            '  sha256sum "$1"; '
            'else exit "$status"; fi',
            "sh",
            remote,
        ],
        check=False,
    )
    if result.returncode == 42 and result.stdout.strip() == "__AOI_SYMLINK__":
        raise RuntimeError(f"远程文件是符号链接，拒绝读取: {remote}")
    if result.returncode == 43 and result.stdout.strip() == "__AOI_NOT_REGULAR__":
        raise RuntimeError(f"远程路径不是普通文件，拒绝读取: {remote}")
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"无法读取远程文件摘要 {remote}: {detail or result.returncode}")
    output = result.stdout.strip()
    if output == "__AOI_ABSENT__":
        return None
    fields = output.split()
    if not fields:
        raise RuntimeError(f"远程文件摘要为空: {remote}")
    digest = fields[0]
    if not _SHA256_RE.fullmatch(digest):
        raise RuntimeError(f"远程文件摘要格式错误: {remote}")
    return digest


def _copy_to(container: str, source: Path, remote: str) -> None:
    parent = posixpath.dirname(remote)
    _assert_no_remote_symlink(container, parent)
    _docker(["exec", container, "mkdir", "-p", parent])
    _assert_no_remote_symlink(container, remote)
    _docker(["cp", str(source), f"{container}:{remote}"])


def _remove(container: str, remote: str) -> None:
    # Removing a final symlink is safe and is required to clean up a path an
    # interrupted attack may have replaced.  Parent symlinks remain forbidden.
    _assert_no_remote_symlink(container, posixpath.dirname(remote))
    _docker(["exec", container, "rm", "-rf", remote])


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether ``path`` is contained by ``root`` after resolution."""

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"场景字段 {field} 必须是非空字符串")
    return value.strip()


def _safe_artifact_path(value: Any, field: str, scenario_root: Path) -> str:
    """Validate a scenario artifact reference without following escapes.

    Artifact names are metadata, but accepting an absolute path or ``..``
    segment makes it too easy for a malformed scenario to select files outside
    its self-contained directory.  Normalize both slash styles because
    scenarios are often generated on Windows and consumed on Linux.
    """

    raw = _nonempty_text(value, field).replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise RuntimeError(f"场景 artifact 路径不得包含控制字符: {field}")
    path = Path(raw)
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", raw):
        raise RuntimeError(f"场景 artifact 路径必须是相对路径: {field}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"场景 artifact 路径不安全: {field}={value}")
    candidate = (scenario_root / Path(*parts)).resolve()
    if not _is_relative_to(candidate, scenario_root):
        raise RuntimeError(f"场景 artifact 路径越界: {field}={value}")
    return "/".join(parts)


def _verify_known_source(
    scenario: dict[str, Any],
    fake_world: dict[str, Any],
    scenario_root: Path,
) -> None:
    """Cross-check source metadata when the referenced source is local.

    A scenario is intentionally portable, so an archived scenario may carry a
    source path that no longer exists on the current host.  When the path is a
    known project-local file, however, silently accepting changed bytes would
    make the recorded source hash meaningless.  External paths are metadata
    only and are never opened by the injector.
    """

    source_ref = _nonempty_text(scenario.get("source_environment"), "source_environment")
    source_path = Path(source_ref)
    if not source_path.is_absolute():
        source_path = scenario_root / source_path
    try:
        source_path = source_path.resolve()
    except OSError:
        return

    known_roots = (PROJECT_ROOT.resolve(), scenario_root)
    if not any(_is_relative_to(source_path, root) for root in known_roots):
        return
    if not source_path.is_file():
        return

    try:
        source_bytes = source_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"无法读取场景 source_environment: {source_path}") from exc
    expected_hash = str(scenario["source_sha256"]).lower()
    actual_hash = hashlib.sha256(source_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(
            "source_environment 内容与 source_sha256 不一致: "
            f"{source_path}"
        )
    try:
        source = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"source_environment 不是合法 JSON: {source_path}") from exc
    if not isinstance(source, dict):
        raise RuntimeError("source_environment JSON 根节点必须是对象")
    if source.get("environment_id") != fake_world["source_environment_id"]:
        raise RuntimeError(
            "source_environment_id 与 source_environment 内容不一致: "
            f"{source.get('environment_id')} != {fake_world['source_environment_id']}"
        )


def _validate_scenario_pair(
    scenario: dict[str, Any],
    fake_world: dict[str, Any],
    scenario_root: Path,
    world_path: Path,
) -> None:
    """Validate the binding between a scenario descriptor and fake world."""

    if scenario.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise RuntimeError(
            f"不支持的 scenario schema_version: {scenario.get('schema_version')!r}"
        )
    if fake_world.get("schema_version") != FAKE_WORLD_SCHEMA_VERSION:
        raise RuntimeError(
            f"不支持的 fake_world schema_version: {fake_world.get('schema_version')!r}"
        )

    scenario_id = _nonempty_text(scenario.get("scenario_id"), "scenario_id")
    if any(ord(char) < 32 or ord(char) == 127 for char in scenario_id):
        raise RuntimeError("scenario_id 不得包含控制字符")
    fake_world_id = _nonempty_text(fake_world.get("fake_world_id"), "fake_world_id")
    if any(ord(char) < 32 or ord(char) == 127 for char in fake_world_id):
        raise RuntimeError("fake_world_id 不得包含控制字符")
    source_id = _nonempty_text(fake_world.get("source_environment_id"), "source_environment_id")

    source_hash = _nonempty_text(scenario.get("source_sha256"), "source_sha256")
    if not _SHA256_RE.fullmatch(source_hash):
        raise RuntimeError("source_sha256 必须是 64 位十六进制摘要")

    selection = scenario.get("selection")
    if not isinstance(selection, dict):
        raise RuntimeError("scenario.selection 必须是对象")
    selected = selection.get("selected_ids")
    if (
        not isinstance(selected, list)
        or not selected
        or any(not isinstance(item, str) or not item.strip() for item in selected)
    ):
        raise RuntimeError("scenario.selection.selected_ids 必须是非重复字符串列表")
    selected = [item.strip() for item in selected]
    if len(selected) != len(set(selected)) or any(any(ord(char) < 32 or ord(char) == 127 for char in item) for item in selected):
        raise RuntimeError("scenario.selection.selected_ids 必须是非重复可打印字符串列表")

    fake_selected = fake_world.get("selected_profiles")
    if fake_selected != selected:
        raise RuntimeError(
            "scenario.selection.selected_ids 与 fake_world.selected_profiles 不一致"
        )

    world = fake_world.get("world")
    if not isinstance(world, dict):
        raise RuntimeError("fake_world.world 必须是对象")
    claims = fake_world.get("claims", {})
    if not isinstance(claims, dict):
        raise RuntimeError("fake_world.claims 必须是对象")
    observations = fake_world.get("observations", [])
    if not isinstance(observations, list):
        raise RuntimeError("fake_world.observations 必须是列表")
    changed = fake_world.get("changed_fields")
    if (
        not isinstance(changed, list)
        or not changed
        or any(not isinstance(item, str) or not item.strip() for item in changed)
        or len(changed) != len(set(changed))
    ):
        raise RuntimeError("fake_world.changed_fields 必须是非重复字符串列表")

    patches = scenario.get("patches")
    if not isinstance(patches, list) or not patches:
        raise RuntimeError("scenario.patches 必须是非空列表")
    patch_ids: list[str] = []
    patch_changed: list[str] = []
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise RuntimeError(f"scenario.patches[{index}] 必须是对象")
        profile_id = _nonempty_text(patch.get("profile_id"), f"patches[{index}].profile_id")
        patch_ids.append(profile_id)
        targets = patch.get("target_paths")
        if not isinstance(targets, list) or any(not isinstance(item, str) or not item.strip() for item in targets):
            raise RuntimeError(f"patches[{index}].target_paths 必须是字符串列表")
        patch_fields = patch.get("changed_fields")
        if not isinstance(patch_fields, list) or any(not isinstance(item, str) or not item.strip() for item in patch_fields):
            raise RuntimeError(f"patches[{index}].changed_fields 必须是字符串列表")
        patch_changed.extend(patch_fields)
    if patch_ids != selected:
        raise RuntimeError("scenario.patches 的 profile_id 顺序必须与 selected_ids 一致")
    if set(patch_changed) != set(changed):
        raise RuntimeError(
            "scenario.patches.changed_fields 与 fake_world.changed_fields 不一致"
        )

    artifacts = scenario.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeError("scenario.artifacts 必须是对象")
    if "fake_world" not in artifacts:
        raise RuntimeError("scenario.artifacts 缺少 fake_world")
    fake_world_ref = _safe_artifact_path(artifacts["fake_world"], "artifacts.fake_world", scenario_root)
    # The public runner and the bundle builder both use this fixed pair.  A
    # nested or alternate path would allow a descriptor and file to diverge.
    if fake_world_ref != "fake_world.json":
        raise RuntimeError("artifacts.fake_world 必须指向场景目录中的 fake_world.json")
    try:
        resolved_world = world_path.resolve()
    except OSError as exc:
        raise RuntimeError("fake_world.json 路径解析失败") from exc
    if not _is_relative_to(resolved_world, scenario_root):
        raise RuntimeError("fake_world.json 不得指向场景目录之外")
    if resolved_world != (scenario_root / fake_world_ref).resolve():
        raise RuntimeError("scenario 与 fake_world artifact 路径不一致")
    for name, reference in artifacts.items():
        _safe_artifact_path(reference, f"artifacts.{name}", scenario_root)

    optional_source_id = scenario.get("source_environment_id")
    if optional_source_id is not None and optional_source_id != source_id:
        raise RuntimeError("scenario.source_environment_id 与 fake_world 不一致")
    _verify_known_source(scenario, fake_world, scenario_root)


def _scenario_files(scenario_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        scenario_root = scenario_dir.resolve()
    except OSError as exc:
        raise RuntimeError(f"场景目录路径解析失败: {scenario_dir}") from exc
    if not scenario_root.is_dir():
        raise RuntimeError(f"场景目录不存在: {scenario_root}")
    scenario_path = scenario_root / "scenario.json"
    world_path = scenario_root / "fake_world.json"
    if not scenario_path.is_file() or not world_path.is_file():
        raise RuntimeError("场景目录必须同时包含 scenario.json 和 fake_world.json")
    for path, label in ((scenario_path, "scenario.json"), (world_path, "fake_world.json")):
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise RuntimeError(f"{label} 路径解析失败") from exc
        if not _is_relative_to(resolved, scenario_root):
            raise RuntimeError(f"{label} 不得指向场景目录之外")
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        fake_world = json.loads(world_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"场景 JSON 格式错误: {exc}") from exc
    if not isinstance(scenario, dict) or not isinstance(fake_world, dict):
        raise RuntimeError("场景 JSON 根节点必须是对象")
    _validate_scenario_pair(scenario, fake_world, scenario_root, world_path)
    return scenario, fake_world


def _target_for(relative: str) -> str:
    relative = _nonempty_text(relative, "relative_path").replace("\\", "/")
    if any(ord(char) < 32 or ord(char) == 127 for char in relative):
        raise RuntimeError(f"不允许的注入路径: {relative}")
    if relative.startswith("/"):
        raise RuntimeError(f"不允许的注入路径: {relative}")
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
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


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"注入清单无法读取或格式错误: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("注入清单根节点必须是对象")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RuntimeError(f"不支持的 injection manifest schema_version: {value.get('schema_version')!r}")
    records = value.get("records")
    if not isinstance(records, list):
        raise RuntimeError("注入清单 records 必须是列表")
    manifest_root = path.resolve().parent
    seen_targets: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise RuntimeError(f"注入清单 records[{index}] 必须是对象")
        relative = _nonempty_text(record.get("relative_path"), f"records[{index}].relative_path")
        expected_target = _target_for(relative)
        if expected_target in seen_targets:
            raise RuntimeError(f"注入清单存在重复路径: {relative}")
        seen_targets.add(expected_target)
        if record.get("target_path") != expected_target:
            raise RuntimeError(f"注入清单目标路径不匹配: {relative}")
        if not isinstance(record.get("existed_before"), bool):
            raise RuntimeError(f"records[{index}].existed_before 必须是布尔值")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise RuntimeError(f"records[{index}].sha256 必须是 64 位十六进制摘要")
        backup = record.get("backup_path")
        if record["existed_before"]:
            if not isinstance(backup, str) or not backup.strip():
                raise RuntimeError(f"records[{index}] 缺少 backup_path")
            backup_path = Path(backup)
            if not backup_path.is_absolute():
                backup_path = manifest_root / backup_path
            backup_path = backup_path.resolve()
            if not _is_relative_to(backup_path, manifest_root):
                raise RuntimeError(f"records[{index}].backup_path 超出运行目录")
            original = record.get("original_sha256")
            if not isinstance(original, str) or not _SHA256_RE.fullmatch(original):
                raise RuntimeError(f"records[{index}].original_sha256 必须是 64 位十六进制摘要")
            # Restore uses the validated canonical path even when an archived
            # manifest stored a path relative to its own run directory.
            record["backup_path"] = str(backup_path)
        elif backup is not None:
            raise RuntimeError(f"records[{index}] 不应包含 backup_path")
    root_record = value.get("observation_root")
    if not isinstance(root_record, dict) or root_record.get("path") != OBSERVATION_ROOT:
        raise RuntimeError("注入清单 observation_root 元数据无效")
    if not isinstance(root_record.get("existed_before"), bool) or not isinstance(root_record.get("created_by_apply"), bool):
        raise RuntimeError("注入清单 observation_root 标志无效")
    if root_record["created_by_apply"] == root_record["existed_before"]:
        raise RuntimeError("注入清单 observation_root 标志互相矛盾")
    return value


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
    if root_record.get("created_by_apply") and _exists(container, OBSERVATION_ROOT):
        # This root is created exclusively by the injector.  The attack model
        # may create additional files below it, so remove the whole dedicated
        # root during recovery instead of treating those files as a failure.
        _remove(container, OBSERVATION_ROOT)


def apply(scenario_dir: Path, container: str, run_dir: Path) -> Path:
    run_dir = run_dir.resolve()
    scenario, fake_world = _scenario_files(scenario_dir.resolve())
    staging = run_dir / "injection_staging"
    if staging.exists():
        if not staging.is_dir():
            raise RuntimeError(f"注入 staging 路径不是目录: {staging}")
        try:
            shutil.rmtree(staging)
        except OSError as exc:
            raise RuntimeError(f"无法清理旧的注入 staging: {staging}") from exc
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
    root_existed = _exists(container, OBSERVATION_ROOT)
    if root_existed:
        raise RuntimeError(f"目标观测目录已经存在，拒绝覆盖: {OBSERVATION_ROOT}")

    manifest_path = run_dir / "injection_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
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
    manifest_path = manifest_path.resolve()
    manifest = _load_manifest(manifest_path)
    container = container or manifest.get("container")
    if not container:
        raise RuntimeError("恢复时缺少容器 ID")
    if manifest.get("status") == "restored":
        # A previously restored manifest is still useful as a verification
        # record.  Re-check the target so a later caller cannot mistake a
        # modified container for the recorded clean state.
        verify(manifest_path, container)
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
    if manifest.get("observation_root", {}).get("created_by_apply") and _exists(container, OBSERVATION_ROOT):
        raise RuntimeError(f"恢复后观测目录仍存在: {OBSERVATION_ROOT}")
    manifest["restored_at"] = _now()
    manifest["status"] = "restored"
    _write_manifest(manifest_path, manifest)


def verify(manifest_path: Path, container: str | None = None) -> None:
    manifest = _load_manifest(manifest_path.resolve())
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
    if restored and manifest.get("observation_root", {}).get("created_by_apply") and _exists(container, OBSERVATION_ROOT):
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
