import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from assistant.tools import WriteFileTool

from main import _build_approver
from support import FakeTool, make_policy


class CliApprovalTests(unittest.TestCase):
    def test_write_approval_prompts_without_printing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            approver = _build_approver(policy)
            output = io.StringIO()

            with patch("builtins.input", return_value="y"):
                with redirect_stdout(output):
                    approved = approver(
                        WriteFileTool.spec,
                        {
                            "path": "notes.txt",
                            "content": "super-secret",
                            "overwrite": False,
                        },
                    )

            self.assertTrue(approved)
            text = output.getvalue()
            self.assertIn("notes.txt", text)
            self.assertIn("12 bytes", text)
            self.assertNotIn("super-secret", text)

    def test_write_approval_is_denied_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = make_policy(Path(tmp))
            approver = _build_approver(policy)

            with patch("builtins.input", return_value="n"):
                with redirect_stdout(io.StringIO()):
                    approved = approver(
                        WriteFileTool.spec,
                        {"path": "notes.txt", "content": "x"},
                    )

            self.assertFalse(approved)

    def test_overwrite_requires_an_explicit_second_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(root)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "notes.txt").write_text(
                "old", encoding="utf-8"
            )
            approver = _build_approver(policy)
            arguments = {
                "path": "notes.txt",
                "content": "new",
                "overwrite": True,
            }

            with patch(
                "builtins.input", side_effect=["y", "n"]
            ):
                with redirect_stdout(io.StringIO()):
                    denied = approver(WriteFileTool.spec, arguments)

            with patch(
                "builtins.input", side_effect=["y", "y"]
            ):
                with redirect_stdout(io.StringIO()):
                    approved = approver(WriteFileTool.spec, arguments)

            self.assertFalse(denied)
            self.assertTrue(approved)

    def test_non_write_tool_uses_a_generic_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = make_policy(Path(tmp))
            approver = _build_approver(policy)
            output = io.StringIO()

            with patch("builtins.input", return_value="yes"):
                with redirect_stdout(output):
                    approved = approver(
                        FakeTool("read_file").spec, {"path": "notes.txt"}
                    )

            self.assertTrue(approved)
            self.assertIn("read_file", output.getvalue())

    def test_approval_fails_closed_on_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = make_policy(Path(tmp))
            approver = _build_approver(policy)

            with patch("builtins.input", side_effect=EOFError):
                with redirect_stdout(io.StringIO()):
                    approved = approver(
                        WriteFileTool.spec,
                        {"path": "notes.txt", "content": "x"},
                    )

            self.assertFalse(approved)

    def test_unsafe_overwrite_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = make_policy(Path(tmp))
            approver = _build_approver(policy)

            with patch("builtins.input", return_value="y"):
                with redirect_stdout(io.StringIO()):
                    approved = approver(
                        WriteFileTool.spec,
                        {
                            "path": "../outside.txt",
                            "content": "x",
                            "overwrite": True,
                        },
                    )

            self.assertFalse(approved)


if __name__ == "__main__":
    unittest.main()
