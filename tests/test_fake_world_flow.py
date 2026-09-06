from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_scenario  # noqa: E402
import live_injector  # noqa: E402
from generate_scenario import (  # noqa: E402
    apply_profile,
    mock_value,
    profile_map,
    source_world,
    validate_selected_profiles,
)
from observation_bundle import build_bundle  # noqa: E402
from renderer import render_html, render_http_header, render_nmap  # noqa: E402
from validator import validate_fake_world, validate_internal_consistency  # noqa: E402


class FakeWorldFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((ROOT / "configs/manipulation_catalog.json").read_text(encoding="utf-8"))
        self.real = json.loads((ROOT / "configs/real_world.json").read_text(encoding="utf-8"))
        self.profiles = profile_map(self.catalog)

    def test_every_catalog_profile_has_mock_adapter(self) -> None:
        fake = {
            "world": source_world(self.real),
            "claims": {},
            "observations": [],
        }
        for index, profile_id in enumerate(self.profiles, start=1):
            value = mock_value(profile_id, source_world(self.real), index)
            changed = apply_profile(fake, self.profiles[profile_id], value, index)
            self.assertTrue(changed, profile_id)
        self.assertEqual(validate_internal_consistency(fake), [])

    def test_bundle_hides_internal_injection_markers(self) -> None:
        fake = {
            "fake_world_id": "fake-test",
            "source_environment_id": "XBEN-028-24",
            "selected_profiles": ["fake_cve"],
            "changed_fields": ["claims.vulnerabilities[]"],
            "world": source_world(self.real),
            "claims": {
                "vulnerabilities": [{"id": "vuln-001", "cve": "CVE-2099-0001", "profile_id": "fake_cve", "synthetic": True}],
            },
            "observations": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_bundle(fake, output)
            public_text = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*" ) if path.is_file())
            self.assertNotIn("fake-test", public_text)
            self.assertNotIn("selected_profiles", public_text)
            self.assertNotIn('"synthetic"', public_text)
            assessment = json.loads((output / "security_assessment.json").read_text(encoding="utf-8"))
            self.assertEqual(assessment["security_assessment"]["vulnerabilities"][0]["cve"], "CVE-2099-0001")

    def test_renderer_escapes_html_and_keeps_headers_single_line(self) -> None:
        world = {
            "web_server": {"name": "</meta><script>", "version": "2.4\r\nInjected: yes"},
            "framework": {"name": 'PHP"', "version": "7.4\nX-Evil: yes"},
            "services": [{"port": "80\r\nX-Port: yes", "protocol": "http"}],
        }
        rendered_html = render_html(world)
        self.assertIn("&lt;/meta&gt;&lt;script&gt;", rendered_html)
        self.assertNotIn("</meta><script>", rendered_html)

        rendered_header = render_http_header(world)
        self.assertEqual(len(rendered_header.splitlines()), 2)
        self.assertNotIn("\r", rendered_header)
        self.assertNotIn("\nInjected:", rendered_header)

        rendered_nmap = render_nmap(world)
        self.assertEqual(len(rendered_nmap.splitlines()), 1)
        self.assertNotIn("\r", rendered_nmap)

    def test_validator_reports_malformed_world_without_crashing(self) -> None:
        errors = validate_internal_consistency(
            {"world": {"web_server": "Apache", "services": [None, "bad"]}}
        )
        self.assertIn("web_server must be a JSON object", errors)
        self.assertIn("services[0] must be a JSON object", errors)
        self.assertIn("services[1] must be a JSON object", errors)

    def test_validator_reconciles_claim_only_v2_changes(self) -> None:
        fake = {
            "schema_version": "aoi.fake-world/v2",
            "source_environment_id": self.real["environment_id"],
            "selected_profiles": ["fake_cve", "policy_stop_testing"],
            "world": source_world(self.real),
            "claims": {
                "vulnerabilities": [{"id": "vuln-001", "cve": "CVE-2099-0001"}],
                "policy": {"task_termination": "Synthetic stop notice"},
            },
            "observations": [],
            "changed_fields": [
                "security_assessment.vulnerabilities[vuln-001].cve",
                "claims.policy.task_termination",
            ],
        }
        errors, actual, declared = validate_fake_world(self.real, fake)
        self.assertEqual(errors, [])
        self.assertEqual(actual, sorted(fake["changed_fields"]))
        self.assertEqual(declared, sorted(fake["changed_fields"]))

    def test_bundle_preserves_colliding_claim_categories(self) -> None:
        fake = {
            "world": source_world(self.real),
            "claims": {
                "a/b": [{"value": "first"}],
                "a_b": [{"value": "second"}],
            },
            "observations": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_bundle(fake, output)
            first = output / "claims" / "a_b.json"
            second = output / "claims" / "a_b-2.json"
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assertEqual(json.loads(first.read_text(encoding="utf-8"))["category"], "a/b")
            self.assertEqual(json.loads(second.read_text(encoding="utf-8"))["category"], "a_b")

    def test_bundle_escapes_plain_page_content(self) -> None:
        fake = {
            "world": source_world(self.real),
            "claims": {},
            "observations": [{"title": "Page", "path": "/page", "content": "<script>alert(1)</script>"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_bundle(fake, output)
            page = (output / "pages" / "page.html").read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
            self.assertNotIn("<script>alert(1)</script>", page)

    def test_id_addressed_vulnerability_target_is_accepted(self) -> None:
        fake = {"world": source_world(self.real), "claims": {}, "observations": []}
        profile = self.profiles["wrong_patch_status"]
        changed = apply_profile(
            fake,
            profile,
            {"target": "vulnerabilities[vuln-001].status", "status": "patched"},
            1,
        )
        self.assertEqual(changed, ["security_assessment.vulnerabilities[vuln-001].status"])

    def test_numeric_bracket_vulnerability_target_is_accepted(self) -> None:
        fake = {"world": source_world(self.real), "claims": {}, "observations": []}
        profile = self.profiles["wrong_patch_status"]
        changed = apply_profile(
            fake,
            profile,
            {"target": "vulnerabilities[0].status", "status": "patched"},
            1,
        )
        self.assertEqual(changed, ["security_assessment.vulnerabilities.0.status"])

    def test_profile_selection_rejects_overlapping_targets(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "目标冲突"):
            validate_selected_profiles(["fake_framework", "fake_version"], self.profiles)

    def test_api_generation_failure_leaves_no_partial_scenario_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "generated"
            argv = [
                "generate_scenario.py",
                "--selection",
                "manual",
                "--mode",
                "api",
                "--select",
                "fake_framework",
                "--output-root",
                str(output_root),
            ]
            with mock.patch.object(generate_scenario, "call_model", side_effect=RuntimeError("model failed")), mock.patch.object(sys, "argv", argv):
                with self.assertRaisesRegex(RuntimeError, "model failed"):
                    generate_scenario.main()
            self.assertFalse(output_root.exists())

    def test_caller_api_alias_overrides_dotenv_canonical_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            (project_root / ".env").write_text(
                "MODEL_API_KEY=dotenv-key\nMODEL_BASE_URL=https://dotenv.example/v1\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(generate_scenario, "ROOT", project_root),
                mock.patch.object(generate_scenario, "_CALLER_ENV_VALUES", None),
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY": "caller-key",
                        "OPENAI_BASE_URL": "https://caller.example/v1",
                    },
                    clear=True,
                ),
            ):
                key, base_url, _ = generate_scenario.model_config()
            self.assertEqual(key, "caller-key")
            self.assertEqual(base_url, "https://caller.example/v1")

    def test_injector_rejects_mismatched_artifact_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "generated"
            argv = [
                "generate_scenario.py",
                "--selection",
                "manual",
                "--mode",
                "mock",
                "--select",
                "fake_framework,fake_cve",
                "--output-root",
                str(output_root),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(generate_scenario.main(), 0)
            scenario_dir = next(output_root.glob("scenario-*"))
            scenario_path = scenario_dir / "scenario.json"
            scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
            scenario["artifacts"]["fake_world"] = "../fake_world.json"
            scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "artifact"):
                live_injector._scenario_files(scenario_dir)

    def test_injector_rejects_manifest_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "injection_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": live_injector.MANIFEST_SCHEMA_VERSION,
                        "observation_root": {
                            "path": live_injector.OBSERVATION_ROOT,
                            "existed_before": False,
                            "created_by_apply": True,
                        },
                        "records": [
                            {
                                "relative_path": "../security_assessment.json",
                                "target_path": live_injector.ASSESSMENT_PATH,
                                "existed_before": False,
                                "backup_path": None,
                                "original_sha256": None,
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "注入路径"):
                live_injector._load_manifest(manifest_path)

    def test_injector_rejects_equivalent_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "injection_manifest.json"
            records = []
            for relative in ("claims/example.json", "claims\\example.json"):
                records.append(
                    {
                        "relative_path": relative,
                        "target_path": live_injector.OBSERVATION_ROOT + "/claims/example.json",
                        "existed_before": False,
                        "backup_path": None,
                        "original_sha256": None,
                        "sha256": "0" * 64,
                    }
                )
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": live_injector.MANIFEST_SCHEMA_VERSION,
                        "observation_root": {
                            "path": live_injector.OBSERVATION_ROOT,
                            "existed_before": False,
                            "created_by_apply": True,
                        },
                        "records": records,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "重复路径"):
                live_injector._load_manifest(manifest_path)

    def test_injector_remote_probe_rejects_symlinks_and_non_regular_files(self) -> None:
        symlink_probe = mock.Mock(returncode=42, stdout="__AOI_SYMLINK__\n", stderr="")
        with mock.patch.object(live_injector, "_docker", return_value=symlink_probe):
            with self.assertRaisesRegex(RuntimeError, "符号链接"):
                live_injector._assert_no_remote_symlink("container", "/var/www/html/link")

        symlink_guard = mock.Mock(returncode=0, stdout="__AOI_NO_SYMLINK__\n", stderr="")
        non_regular_probe = mock.Mock(returncode=43, stdout="__AOI_NOT_REGULAR__\n", stderr="")
        with mock.patch.object(live_injector, "_docker", side_effect=[symlink_guard, non_regular_probe]):
            with self.assertRaisesRegex(RuntimeError, "普通文件"):
                live_injector._remote_sha256("container", "/var/www/html/item")


if __name__ == "__main__":
    unittest.main()
