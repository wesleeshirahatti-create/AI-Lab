import tempfile
import unittest
from pathlib import Path

from assistant.tools import (
    ListFilesTool,
    ReadFileTool,
    ToolGateway,
    ToolValidationError,
    build_default_registry,
)

from support import make_audit, make_policy


class ListFilesToolTests(unittest.TestCase):
    def test_lists_workspace_entries_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta.txt").write_text("b", encoding="utf-8")
            (root / "alpha.txt").write_text("a", encoding="utf-8")
            (root / "subdir").mkdir()
            tool = ListFilesTool(root)

            result = tool.execute({})

            self.assertTrue(result.ok)
            self.assertEqual(
                result.output.splitlines(), ["alpha.txt", "beta.txt", "subdir"]
            )
            self.assertEqual(result.metadata["count"], 3)
            self.assertFalse(result.metadata["truncated"])

    def test_lists_requested_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subdir = root / "docs"
            subdir.mkdir()
            (subdir / "notes.txt").write_text("notes", encoding="utf-8")
            tool = ListFilesTool(root)

            result = tool.execute({"path": "docs"})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "notes.txt")

    def test_list_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                (root / f"file{index}.txt").write_text("x", encoding="utf-8")
            tool = ListFilesTool(root, max_list_entries=2)

            result = tool.execute({"path": "."})

            self.assertTrue(result.ok)
            self.assertEqual(len(result.output.splitlines()), 2)
            self.assertEqual(result.metadata["total_count"], 5)
            self.assertTrue(result.metadata["truncated"])

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (root.parent / "outside.txt").write_text("secret", encoding="utf-8")
            tool = ListFilesTool(root)

            result = tool.execute({"path": "../"})

            self.assertFalse(result.ok)
            self.assertIn("traversal", result.error.lower())
            with self.assertRaises(ToolValidationError):
                tool.validate({"path": "../"})

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = ListFilesTool(root)

            for path in (str(root), "/", "\\"):
                with self.subTest(path=path):
                    result = tool.execute({"path": path})
                    self.assertFalse(result.ok)
                    self.assertIn("not allowed", result.error.lower())

    def test_rejects_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            tool = ListFilesTool(root)

            result = tool.execute({"path": "notes.txt"})

            self.assertFalse(result.ok)
            self.assertIn("not a directory", result.error.lower())

    def test_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = ListFilesTool(Path(tmp))

            with self.assertRaises(ToolValidationError):
                tool.validate({"path": 123})
            with self.assertRaises(ToolValidationError):
                tool.validate({"unknown": "value"})


class ReadFileToolTests(unittest.TestCase):
    def test_reads_utf8_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("hello world", encoding="utf-8")
            tool = ReadFileTool(root)

            result = tool.execute({"path": "notes.txt"})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "hello world")
            self.assertEqual(result.metadata["path"], "notes.txt")
            self.assertFalse(result.metadata["truncated"])

    def test_max_chars_truncates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("abcdefghij", encoding="utf-8")
            tool = ReadFileTool(root)

            result = tool.execute({"path": "notes.txt", "max_chars": 4})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "abcd")
            self.assertEqual(result.metadata["total_chars"], 10)
            self.assertTrue(result.metadata["truncated"])

    def test_empty_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty.txt").write_text("", encoding="utf-8")
            tool = ReadFileTool(root)

            result = tool.execute({"path": "empty.txt"})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "")
            self.assertEqual(result.metadata["chars"], 0)

    def test_rejects_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            tool = ReadFileTool(root)

            for path in (str(root / "notes.txt"), "/etc/passwd"):
                with self.subTest(path=path):
                    result = tool.execute({"path": path})
                    self.assertFalse(result.ok)
                    self.assertIn("not allowed", result.error.lower())

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            root.mkdir()
            (root.parent / "outside.txt").write_text("secret", encoding="utf-8")
            tool = ReadFileTool(root)

            for path in ("../outside.txt", "sub/../../outside.txt"):
                with self.subTest(path=path):
                    result = tool.execute({"path": path})
                    self.assertFalse(result.ok)
                    self.assertIn("traversal", result.error.lower())
                    with self.assertRaises(ToolValidationError):
                        tool.validate({"path": path})

    def test_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is not available.")

            tool = ReadFileTool(root)
            result = tool.execute({"path": "link/secret.txt"})

            self.assertFalse(result.ok)
            self.assertIn("outside the workspace", result.error.lower())

    def test_rejects_binary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "image.bin").write_bytes(b"header\x00binary\x00data")
            tool = ReadFileTool(root)

            result = tool.execute({"path": "image.bin"})

            self.assertFalse(result.ok)
            self.assertIn("binary", result.error.lower())

    def test_rejects_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.bin").write_bytes(b"\xff\xfe\xfa\xfb")
            tool = ReadFileTool(root)

            result = tool.execute({"path": "invalid.bin"})

            self.assertFalse(result.ok)
            self.assertIn("binary", result.error.lower())

    def test_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_bytes(b"a" * 1025)
            tool = ReadFileTool(
                root, max_file_bytes=1024, max_output_bytes=8192
            )

            result = tool.execute({"path": "large.txt"})

            self.assertFalse(result.ok)
            self.assertIn("too large", result.error.lower())

    def test_rejects_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            tool = ReadFileTool(root)

            result = tool.execute({"path": "docs"})

            self.assertFalse(result.ok)
            self.assertIn("not a file", result.error.lower())

    def test_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = ReadFileTool(Path(tmp))

            invalid = [
                {},
                {"path": ""},
                {"path": 42},
                {"path": "notes.txt", "max_chars": "10"},
                {"path": "notes.txt", "max_chars": True},
                {"path": "notes.txt", "max_chars": 0},
                {"path": "notes.txt", "extra": True},
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolValidationError):
                        tool.validate(arguments)

    def test_max_chars_is_clamped_to_output_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = ReadFileTool(
                Path(tmp), max_output_bytes=1024
            )
            validated = tool.validate(
                {"path": "notes.txt", "max_chars": 999999}
            )
        self.assertEqual(validated["max_chars"], 1024)


class FileToolGatewayTests(unittest.TestCase):
    def _gateway(self, root, policy, approver=None):
        registry = build_default_registry(policy=policy)
        audit = make_audit(root)
        gateway = ToolGateway(
            policy, registry, audit, approver=approver
        )
        return gateway, audit

    def test_tools_disabled_by_default_denies_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            policy = make_policy(root, tools_enabled=False)
            gateway, _ = self._gateway(root, policy)

            result = gateway.dispatch("read_file", {"path": "notes.txt"})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Tools are disabled.")

    def test_read_file_is_auto_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            policy = make_policy(root)
            gateway, audit = self._gateway(root, policy)

            result = gateway.dispatch("read_file", {"path": "notes.txt"})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "notes")
            events = [
                line
                for line in audit.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(events), 1)

    def test_permission_denial_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            policy = make_policy(root, auto_approve_reads=False)
            gateway, _ = self._gateway(
                root, policy, approver=lambda spec, arguments: False
            )

            result = gateway.dispatch("read_file", {"path": "notes.txt"})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Permission denied.")

    def test_no_approver_denies_instead_of_model_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            policy = make_policy(root, auto_approve_reads=False)
            gateway, _ = self._gateway(root, policy)

            result = gateway.dispatch("read_file", {"path": "notes.txt"})

            self.assertFalse(result.ok)
            self.assertIn("no approver", result.error.lower())

    def test_gateway_reports_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            gateway, _ = self._gateway(root, policy)

            result = gateway.dispatch(
                "read_file", {"path": "notes.txt", "max_chars": "many"}
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Invalid tool arguments.")


if __name__ == "__main__":
    unittest.main()
