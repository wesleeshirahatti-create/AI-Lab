import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.tools import (
    DEFAULT_MAX_WRITE_BYTES,
    ToolGateway,
    ToolValidationError,
    WriteFileTool,
    build_default_registry,
)

from support import make_audit, make_policy, read_events


def _workspace(tmp):
    root = Path(tmp) / "workspace"
    root.mkdir()
    return root


def _gateway(root, policy, approver=None):
    registry = build_default_registry(policy=policy)
    audit = make_audit(root)
    gateway = ToolGateway(policy, registry, audit, approver=approver)
    return gateway, audit


def _log_text(audit):
    return audit.path.read_text(encoding="utf-8")


def _make_directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0 and link.exists():
            return True
    return False


class WriteFileToolCreationTests(unittest.TestCase):
    def test_creates_file_in_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            result = tool.execute(
                {"path": "notes.txt", "content": "hello world"}
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                (root / "notes.txt").read_text(encoding="utf-8"),
                "hello world",
            )
            self.assertEqual(result.metadata["path"], "notes.txt")
            self.assertEqual(result.metadata["bytes"], 11)
            self.assertTrue(result.metadata["created"])
            self.assertFalse(result.metadata["overwritten"])

    def test_creates_nested_file_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            result = tool.execute(
                {"path": "docs/notes.txt", "content": "nested"}
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                (root / "docs" / "notes.txt").read_text(encoding="utf-8"),
                "nested",
            )

    def test_creates_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            result = tool.execute({"path": "empty.txt", "content": ""})

            self.assertTrue(result.ok)
            self.assertEqual((root / "empty.txt").read_bytes(), b"")

    def test_accepts_exact_64_kb_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)
            content = "a" * DEFAULT_MAX_WRITE_BYTES

            result = tool.execute({"path": "large.txt", "content": content})

            self.assertTrue(result.ok)
            self.assertEqual(
                (root / "large.txt").stat().st_size,
                DEFAULT_MAX_WRITE_BYTES,
            )

    def test_content_limit_counts_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)
            content = "\u00e9" * (DEFAULT_MAX_WRITE_BYTES // 2 + 1)

            result = tool.execute({"path": "large.txt", "content": content})

            self.assertFalse(result.ok)
            self.assertIn("too large", result.error.lower())
            self.assertFalse((root / "large.txt").exists())


class WriteFileToolValidationTests(unittest.TestCase):
    def test_rejects_invalid_argument_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = WriteFileTool(_workspace(tmp))

            invalid = [
                None,
                {},
                {"path": "", "content": "x"},
                {"path": "   ", "content": "x"},
                {"path": 123, "content": "x"},
                {"path": Path("notes.txt"), "content": "x"},
                {"path": "notes.txt"},
                {"path": "notes.txt", "content": None},
                {"path": "notes.txt", "content": 123},
                {"path": "notes.txt", "content": b"bytes"},
                {"path": "notes.txt", "content": "x", "overwrite": "yes"},
                {"path": "notes.txt", "content": "x", "overwrite": 1},
                {"path": "notes.txt", "content": "x", "overwrite": None},
                {"path": "notes.txt", "content": "x", "extra": True},
                {"path": "notes.txt", "content": "\ud800"},
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolValidationError):
                        tool.validate(arguments)

    def test_execute_returns_failure_for_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = WriteFileTool(_workspace(tmp))

            result = tool.execute(
                {"path": "notes.txt", "content": 123}
            )

            self.assertFalse(result.ok)
            self.assertIn("content", result.error.lower())

    def test_rejects_oversized_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            result = tool.execute(
                {
                    "path": "large.txt",
                    "content": "a" * (DEFAULT_MAX_WRITE_BYTES + 1),
                }
            )

            self.assertFalse(result.ok)
            self.assertIn("too large", result.error.lower())
            self.assertFalse((root / "large.txt").exists())

    def test_rejects_absolute_and_home_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            for path in (
                str(root / "notes.txt"),
                "/etc/passwd",
                "\\Windows\\System32\\evil.txt",
                "C:\\Windows\\System32\\evil.txt",
                "C:evil.txt",
                "\\\\server\\share\\evil.txt",
                "~/evil.txt",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)
                    self.assertIn("not allowed", result.error.lower())

    def test_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root.parent / "outside.txt").write_text(
                "secret", encoding="utf-8"
            )
            tool = WriteFileTool(root)

            for path in (
                "../outside.txt",
                "sub/../../outside.txt",
                "..\\outside.txt",
                "sub\\..\\..\\outside.txt",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "overwritten"}
                    )
                    self.assertFalse(result.ok)
                    self.assertIn("traversal", result.error.lower())

            self.assertEqual(
                (root.parent / "outside.txt").read_text(encoding="utf-8"),
                "secret",
            )

    def test_rejects_windows_reserved_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            for path in (
                "CON",
                "con.txt",
                "NUL",
                "AUX.txt",
                "COM1.txt",
                "LPT9.log",
                "sub/PRN.txt",
                "sub/aux/file.txt",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)
                    self.assertIn("reserved", result.error.lower())

    def test_rejects_alternate_data_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            for path in (
                "notes.txt:stream",
                "notes.txt::$DATA",
                "sub/notes.txt:secret",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)
                    self.assertIn(
                        "alternate data stream", result.error.lower()
                    )

    def test_rejects_unsafe_path_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            for path in (
                "bad<name.txt",
                "bad>name.txt",
                "bad|name.txt",
                "bad?name.txt",
                "bad*name.txt",
                'bad"name.txt',
                "bad\x00name.txt",
                "bad\nname.txt",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)

    def test_rejects_trailing_dots_and_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            for path in (
                "notes.txt.",
                "notes.txt ",
                "sub /notes.txt",
                " notes.txt",
                "sub/notes.txt ",
            ):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)

    def test_rejects_directory_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "docs").mkdir()
            tool = WriteFileTool(root)

            for path in (".", "docs", "docs/"):
                with self.subTest(path=path):
                    result = tool.execute(
                        {"path": path, "content": "x"}
                    )
                    self.assertFalse(result.ok)

    def test_rejects_symlink_directory_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _workspace(tmp)
            outside = base / "outside"
            outside.mkdir()
            link = root / "link"
            if not _make_directory_link(link, outside):
                self.skipTest("Link creation is not available.")

            tool = WriteFileTool(root)
            result = tool.execute(
                {"path": "link/evil.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertIn("outside the workspace", result.error.lower())
            self.assertFalse((outside / "evil.txt").exists())

    def test_rejects_symlink_file_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = _workspace(tmp)
            outside = base / "outside"
            outside.mkdir()
            outside_file = outside / "secret.txt"
            outside_file.write_text("secret", encoding="utf-8")
            link = root / "link"
            if not _make_directory_link(link, outside):
                self.skipTest("Link creation is not available.")

            tool = WriteFileTool(root)
            result = tool.execute(
                {
                    "path": "link/secret.txt",
                    "content": "overwritten",
                    "overwrite": True,
                }
            )

            self.assertFalse(result.ok)
            self.assertIn("outside the workspace", result.error.lower())
            self.assertEqual(
                outside_file.read_text(encoding="utf-8"), "secret"
            )

    def test_allows_symlink_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            target_dir = root / "real"
            target_dir.mkdir()
            link = root / "link"
            if not _make_directory_link(link, target_dir):
                self.skipTest("Link creation is not available.")

            tool = WriteFileTool(root)
            result = tool.execute(
                {"path": "link/notes.txt", "content": "inside"}
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                (target_dir / "notes.txt").read_text(encoding="utf-8"),
                "inside",
            )


class WriteFileToolOverwriteTests(unittest.TestCase):
    def test_existing_file_is_not_overwritten_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "notes.txt").write_text("old", encoding="utf-8")
            tool = WriteFileTool(root)

            result = tool.execute(
                {"path": "notes.txt", "content": "new"}
            )

            self.assertFalse(result.ok)
            self.assertIn("already exists", result.error.lower())
            self.assertEqual(
                (root / "notes.txt").read_text(encoding="utf-8"), "old"
            )

    def test_overwrite_true_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "notes.txt").write_text("old", encoding="utf-8")
            tool = WriteFileTool(root)

            result = tool.execute(
                {
                    "path": "notes.txt",
                    "content": "new",
                    "overwrite": True,
                }
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                (root / "notes.txt").read_text(encoding="utf-8"), "new"
            )
            self.assertFalse(result.metadata["created"])
            self.assertTrue(result.metadata["overwritten"])


class WriteFileToolAtomicFailureTests(unittest.TestCase):
    def test_replace_failure_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            (root / "notes.txt").write_text("old", encoding="utf-8")
            tool = WriteFileTool(root)

            with patch(
                "assistant.tools.files.os.replace",
                side_effect=OSError("replace failed"),
            ):
                result = tool.execute(
                    {
                        "path": "notes.txt",
                        "content": "new",
                        "overwrite": True,
                    }
                )

            self.assertFalse(result.ok)
            self.assertIn("could not write", result.error.lower())
            self.assertEqual(
                (root / "notes.txt").read_text(encoding="utf-8"), "old"
            )
            self.assertEqual(list(root.glob(".write-*.tmp")), [])

    def test_create_failure_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            with patch(
                "assistant.tools.files.os.link",
                side_effect=OSError("link failed"),
            ):
                result = tool.execute(
                    {"path": "notes.txt", "content": "new"}
                )

            self.assertFalse(result.ok)
            self.assertFalse((root / "notes.txt").exists())
            self.assertEqual(list(root.glob(".write-*.tmp")), [])

    def test_fsync_failure_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            with patch(
                "assistant.tools.files.os.fsync",
                side_effect=OSError("fsync failed"),
            ):
                result = tool.execute(
                    {"path": "notes.txt", "content": "new"}
                )

            self.assertFalse(result.ok)
            self.assertFalse((root / "notes.txt").exists())
            self.assertEqual(list(root.glob(".write-*.tmp")), [])

    def test_temp_creation_failure_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _workspace(tmp)
            tool = WriteFileTool(root)

            with patch(
                "assistant.tools.files.tempfile.mkstemp",
                side_effect=OSError("temp failed"),
            ):
                result = tool.execute(
                    {"path": "notes.txt", "content": "new"}
                )

            self.assertFalse(result.ok)
            self.assertFalse((root / "notes.txt").exists())


class WriteFileGatewayTests(unittest.TestCase):
    def test_write_is_denied_when_writes_are_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root, allow_writes=False)
            gateway, audit = _gateway(root, policy)

            result = gateway.dispatch(
                "write_file", {"path": "notes.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Write tools are disabled.")
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "denied")
            self.assertEqual(event["tool"], "write_file")

    def test_write_is_denied_without_an_approver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            gateway, audit = _gateway(root, policy)

            result = gateway.dispatch(
                "write_file", {"path": "notes.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertIn("no approver", result.error.lower())
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "denied")

    def test_write_is_denied_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            gateway, audit = _gateway(
                root, policy, approver=lambda spec, arguments: False
            )

            result = gateway.dispatch(
                "write_file", {"path": "notes.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Permission denied.")
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "denied")

    def test_write_is_denied_when_approver_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)

            def approver(spec, arguments):
                raise RuntimeError("prompt failed")

            gateway, audit = _gateway(root, policy, approver=approver)

            result = gateway.dispatch(
                "write_file", {"path": "notes.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Approval failed.")
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "denied")
            self.assertEqual(event["error_type"], "RuntimeError")

    def test_approved_write_is_executed_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            seen = []

            def approver(spec, arguments):
                seen.append((spec.name, dict(arguments)))
                return True

            gateway, audit = _gateway(root, policy, approver=approver)

            result = gateway.dispatch(
                "write_file",
                {"path": "notes.txt", "content": "super-secret"},
            )

            self.assertTrue(result.ok)
            self.assertEqual(
                (root / "workspace" / "notes.txt").read_text(
                    encoding="utf-8"
                ),
                "super-secret",
            )
            self.assertEqual(seen[0][0], "write_file")
            self.assertEqual(seen[0][1]["path"], "notes.txt")
            self.assertFalse(seen[0][1]["overwrite"])

            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "approved")
            self.assertTrue(event["ok"])
            self.assertEqual(event["metadata"]["path"], "notes.txt")
            self.assertEqual(event["metadata"]["bytes"], 12)
            self.assertTrue(event["metadata"]["created"])
            self.assertIn("sha256", event["arguments"]["content"])
            self.assertNotIn("super-secret", _log_text(audit))

    def test_write_content_is_never_logged_even_with_previews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root, log_content=True)
            gateway, audit = _gateway(
                root, policy, approver=lambda spec, arguments: True
            )

            result = gateway.dispatch(
                "write_file",
                {"path": "notes.txt", "content": "super-secret"},
            )

            self.assertTrue(result.ok)
            self.assertNotIn("super-secret", _log_text(audit))
            event = read_events(audit.path)[0]
            self.assertIn("sha256", event["arguments"]["content"])
            self.assertNotIn("preview", event["arguments"]["content"])

    def test_overwrite_protection_and_approved_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "notes.txt").write_text(
                "old", encoding="utf-8"
            )
            seen = []

            def approver(spec, arguments):
                seen.append(dict(arguments))
                return True

            gateway, audit = _gateway(root, policy, approver=approver)

            protected = gateway.dispatch(
                "write_file",
                {"path": "notes.txt", "content": "new"},
            )
            self.assertFalse(protected.ok)
            self.assertIn("already exists", protected.error.lower())
            self.assertEqual(
                (workspace / "notes.txt").read_text(encoding="utf-8"),
                "old",
            )

            replaced = gateway.dispatch(
                "write_file",
                {
                    "path": "notes.txt",
                    "content": "new",
                    "overwrite": True,
                },
            )
            self.assertTrue(replaced.ok)
            self.assertEqual(
                (workspace / "notes.txt").read_text(encoding="utf-8"),
                "new",
            )
            self.assertTrue(seen[-1]["overwrite"])
            events = read_events(audit.path)
            self.assertEqual(events[-1]["permission"], "approved")
            self.assertTrue(events[-1]["metadata"]["overwritten"])

    def test_overwrite_is_denied_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "notes.txt").write_text(
                "old", encoding="utf-8"
            )
            gateway, audit = _gateway(
                root, policy, approver=lambda spec, arguments: False
            )

            result = gateway.dispatch(
                "write_file",
                {
                    "path": "notes.txt",
                    "content": "new",
                    "overwrite": True,
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Permission denied.")
            self.assertEqual(
                (workspace / "notes.txt").read_text(encoding="utf-8"),
                "old",
            )
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "denied")

    def test_invalid_path_is_rejected_before_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            calls = []

            def approver(spec, arguments):
                calls.append(arguments)
                return True

            gateway, audit = _gateway(root, policy, approver=approver)

            result = gateway.dispatch(
                "write_file",
                {"path": "../outside.txt", "content": "x"},
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Invalid tool arguments.")
            self.assertEqual(calls, [])
            self.assertFalse((root.parent / "outside.txt").exists())
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "invalid_arguments")
            self.assertIn("traversal", event["error_detail"].lower())

    def test_oversized_content_is_rejected_before_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            calls = []

            def approver(spec, arguments):
                calls.append(arguments)
                return True

            gateway, audit = _gateway(root, policy, approver=approver)

            result = gateway.dispatch(
                "write_file",
                {
                    "path": "large.txt",
                    "content": "a" * (DEFAULT_MAX_WRITE_BYTES + 1),
                },
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Invalid tool arguments.")
            self.assertEqual(calls, [])
            event = read_events(audit.path)[0]
            self.assertEqual(event["permission"], "invalid_arguments")
            self.assertIn("too large", event["error_detail"].lower())

    def test_write_requires_approval_when_confirmation_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root, require_confirmation=False)
            gateway, _ = _gateway(root, policy)

            result = gateway.dispatch(
                "write_file", {"path": "notes.txt", "content": "x"}
            )

            self.assertFalse(result.ok)
            self.assertIn("no approver", result.error.lower())
            self.assertFalse((root / "workspace" / "notes.txt").exists())


if __name__ == "__main__":
    unittest.main()
