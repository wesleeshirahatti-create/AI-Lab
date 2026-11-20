import json
import tempfile
import unittest
from pathlib import Path

from assistant.tools import AuditLogger


class AuditLoggerTests(unittest.TestCase):
    def test_creates_parent_and_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "audit.jsonl"
            logger = AuditLogger(path)

            self.assertTrue(logger.log({"event": "first", "n": 1}))
            self.assertTrue(logger.log({"event": "second", "n": 2}))

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            events = [json.loads(line) for line in lines]
            self.assertEqual([event["n"] for event in events], [1, 2])
            self.assertIn("timestamp", events[0])
            self.assertIsNone(logger.last_error)

    def test_preserves_existing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            path.write_text('{"existing": true}\n', encoding="utf-8")
            logger = AuditLogger(path)

            self.assertTrue(logger.log({"event": "new"}))

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(json.loads(lines[0])["existing"])
            self.assertEqual(json.loads(lines[1])["event"], "new")

    def test_disabled_logger_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path, enabled=False)

            self.assertTrue(logger.log({"event": "ignored"}))
            self.assertFalse(path.exists())

    def test_oserror_is_reported_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            path.mkdir()
            logger = AuditLogger(path)

            self.assertFalse(logger.log({"event": "cannot write"}))
            self.assertIsNotNone(logger.last_error)

    def test_non_mapping_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)

            self.assertFalse(logger.log(["not", "a", "mapping"]))
            self.assertIsNotNone(logger.last_error)
            self.assertFalse(path.exists())

    def test_non_serializable_values_use_string_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)

            self.assertTrue(logger.log({"path": Path("notes.txt")}))
            event = json.loads(
                path.read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertIsInstance(event["path"], str)

    def test_lone_surrogate_is_escaped_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)

            self.assertTrue(logger.log({"value": "\ud800"}))
            text = path.read_text(encoding="utf-8")
            self.assertIn("\\ud800", text)
            self.assertEqual(json.loads(text)["value"], "\ud800")
            self.assertIsNone(logger.last_error)


if __name__ == "__main__":
    unittest.main()
