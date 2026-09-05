from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_scenario import apply_profile, mock_value, profile_map, source_world  # noqa: E402
from observation_bundle import build_bundle  # noqa: E402
from validator import validate_internal_consistency  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
