from __future__ import annotations

import re
import unittest

from tests.support import ROOT


class DeploymentTests(unittest.TestCase):
    def test_worker_acl_sections_cannot_publish_firmware_or_gui_events(self):
        acl = (ROOT / "deploy" / "mosquitto" / "acl.example").read_text(encoding="utf-8")
        sections = re.split(r"(?m)^user ", acl)
        worker_sections = [
            section
            for section in sections
            if section.startswith(("desk-buddy-detector\n", "desk-buddy-depth\n", "desk-buddy-planner\n"))
        ]
        self.assertEqual(len(worker_sections), 3)
        for section in worker_sections:
            publish_topics = [
                line.split(maxsplit=2)[2]
                for line in section.splitlines()
                if line.startswith(("topic write ", "topic readwrite "))
            ]
            self.assertFalse(any(topic.endswith("/test") for topic in publish_topics))
            self.assertFalse(any("/+/vision/event" in topic for topic in publish_topics))

    def test_example_configs_use_distinct_broker_credentials(self):
        configs = (
            ROOT / "coordinator" / "config.example.toml",
            ROOT / "services" / "detector_huggingface" / "config.example.toml",
            ROOT / "services" / "depth_huggingface" / "config.example.toml",
            ROOT / "services" / "planner_training" / "config.example.toml",
        )
        usernames = set()
        for path in configs:
            match = re.search(r'^username_env = "([^"]+)"$', path.read_text(encoding="utf-8"), re.MULTILINE)
            self.assertIsNotNone(match)
            usernames.add(match.group(1))
        self.assertEqual(len(usernames), len(configs))


if __name__ == "__main__":
    unittest.main()
