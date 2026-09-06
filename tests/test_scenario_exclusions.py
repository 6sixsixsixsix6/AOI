from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_scenario  # noqa: E402


class ScenarioExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = generate_scenario.profile_map(
            generate_scenario.load_json(ROOT / "configs/manipulation_catalog.json")
        )

    def selection_args(self, **overrides: object) -> argparse.Namespace:
        values = dict(select="", exclude="fake_flag,fake_cve", selection="qwen", max=3)
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_ai_candidates_preserve_every_allowed_profile(self) -> None:
        response = {"selected": [{"id": "fake_framework", "reason": "test"}]}
        with mock.patch.object(generate_scenario, "call_model", return_value=response) as api:
            selected, record = generate_scenario.choose_profiles(
                self.selection_args(exclude=" fake_flag, ,fake_cve, "), self.profiles
            )
        prompt = api.call_args.args[0][1]["content"]
        candidates = json.loads(prompt.split("\u5019\u9009\uff1a", 1)[1])
        self.assertEqual(
            {item["id"] for item in candidates},
            set(self.profiles) - {"fake_flag", "fake_cve"},
        )
        self.assertEqual(selected, ["fake_framework"])
        self.assertEqual(record["excluded_ids"], ["fake_flag", "fake_cve"])

    def test_excluded_model_selection_is_rejected(self) -> None:
        for profile_id in ("fake_flag", "fake_cve"):
            with self.subTest(profile_id=profile_id), mock.patch.object(
                generate_scenario, "call_model", return_value={"selected": [{"id": profile_id}]}
            ):
                with self.assertRaisesRegex(RuntimeError, profile_id):
                    generate_scenario.choose_profiles(self.selection_args(), self.profiles)

    def test_invalid_exclusions_fail_before_api_call(self) -> None:
        cases = [
            {"exclude": "unknown_profile"},
            {"exclude": "fake_flag, fake_flag"},
            {"exclude": ",".join(self.profiles)},
            {"select": "fake_flag", "selection": "manual"},
            {"select": "fake_cve"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), mock.patch.object(generate_scenario, "call_model") as api:
                with self.assertRaises(RuntimeError):
                    generate_scenario.choose_profiles(self.selection_args(**overrides), self.profiles)
                api.assert_not_called()

    def test_manual_selection_records_exclusions(self) -> None:
        with mock.patch.object(generate_scenario, "call_model") as api:
            selected, record = generate_scenario.choose_profiles(
                self.selection_args(selection="manual", select="fake_framework"), self.profiles
            )
        api.assert_not_called()
        self.assertEqual(selected, ["fake_framework"])
        self.assertEqual(record["excluded_ids"], ["fake_flag", "fake_cve"])

    def run_generator(self, project: Path, output: str, *extra: str) -> dict:
        output_root = project / output
        argv = [
            "generate_scenario.py", "--selection", "qwen", "--mode", "mock",
            "--source", str(ROOT / "configs/real_world.json"),
            "--output-root", str(output_root), *extra,
        ]
        response = {"selected": [{"id": "fake_framework"}]}
        with (
            mock.patch.object(generate_scenario, "ROOT", project),
            mock.patch.object(generate_scenario, "call_model", return_value=response),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(generate_scenario.main(), 0)
        scenario_dir = next(output_root.glob("scenario-*"))
        scenario = generate_scenario.load_json(scenario_dir / "scenario.json")
        self.assertEqual(
            generate_scenario.load_json(scenario_dir / "selection.json"), scenario["selection"]
        )
        fake_world = generate_scenario.load_json(scenario_dir / "fake_world.json")
        self.assertEqual(fake_world["selected_profiles"], ["fake_framework"])
        self.assertEqual([patch["profile_id"] for patch in scenario["patches"]], ["fake_framework"])
        return scenario["selection"]

    def test_dotenv_is_reread_and_overrides_stale_shell_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"AOI_SCENARIO_EXCLUDE": "fake_secret"}, clear=True
        ):
            project = Path(directory)
            for index, value in enumerate(("fake_flag,fake_cve", "fake_cve", "")):
                with self.subTest(value=value):
                    (project / ".env").write_text(
                        f'export AOI_SCENARIO_EXCLUDE="{value}"\n', encoding="utf-8"
                    )
                    record = self.run_generator(project, f"run-{index}")
                    self.assertEqual(record["excluded_ids"], value.split(",") if value else [])

    def test_cli_overrides_dotenv_including_explicit_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            project = Path(directory)
            (project / ".env").write_text("AOI_SCENARIO_EXCLUDE=fake_flag,fake_cve\n", encoding="utf-8")
            for index, value in enumerate(("fake_secret", "")):
                with self.subTest(value=value):
                    record = self.run_generator(project, f"run-{index}", "--exclude", value)
                    self.assertEqual(record["excluded_ids"], [value] if value else [])

    def test_environment_fallback_and_default_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for index, env in enumerate(({}, {"AOI_SCENARIO_EXCLUDE": "fake_flag"})):
                with self.subTest(env=env), mock.patch.dict(os.environ, env, clear=True):
                    record = self.run_generator(project, f"run-{index}")
                    self.assertEqual(record["excluded_ids"], ["fake_flag"] if env else [])

    def test_dotenv_exclusions_are_enforced_before_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            project = Path(directory)
            (project / ".env").write_text("AOI_SCENARIO_EXCLUDE=fake_framework\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "fake_framework"):
                self.run_generator(project, "generated")
            self.assertFalse((project / "generated").exists())


if __name__ == "__main__":
    unittest.main()
