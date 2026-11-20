import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.tools import ToolPolicy, ToolSpec
from assistant.tools.policy import PROJECT_ROOT


def _spec(name: str, **overrides) -> ToolSpec:
    values = {
        "name": name,
        "description": "A test tool.",
        "parameters": {"type": "object"},
    }
    values.update(overrides)
    return ToolSpec(**values)


class ToolPolicyDefaultsTests(unittest.TestCase):
    def test_tools_are_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(os.environ, {}, clear=True):
                policy = ToolPolicy.from_env(project_root=root)

        self.assertFalse(policy.tools_enabled)
        self.assertFalse(policy.allow_writes)
        self.assertFalse(policy.allow_python)
        self.assertTrue(policy.auto_approve_reads)
        self.assertTrue(policy.require_confirmation)
        self.assertFalse(policy.log_content)
        self.assertEqual(policy.read_root, root)
        self.assertEqual(policy.write_root, root / "workspace")
        self.assertEqual(
            policy.log_path, root / "logs" / "tool_calls.jsonl"
        )

    def test_default_project_root_is_ai_assistant(self):
        with patch.dict(os.environ, {}, clear=True):
            policy = ToolPolicy.from_env()
        self.assertEqual(policy.project_root, PROJECT_ROOT)
        self.assertEqual(policy.read_root, PROJECT_ROOT)

    def test_environment_values_are_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "TOOLS_ENABLED": "true",
                "TOOL_ALLOW_WRITES": "yes",
                "TOOL_ALLOW_PYTHON": "1",
                "TOOL_AUTO_APPROVE_READS": "false",
                "TOOL_REQUIRE_CONFIRMATION": "off",
                "TOOL_LOG_CONTENT": "on",
                "TOOL_READ_ROOT": "data",
                "TOOL_WRITE_ROOT": "sandbox",
                "TOOL_LOG_PATH": "audit/events.jsonl",
                "TOOL_MAX_FILE_BYTES": "2048",
                "TOOL_MAX_OUTPUT_BYTES": "4096",
                "TOOL_MAX_CODE_BYTES": "8192",
                "TOOL_MAX_LIST_ENTRIES": "50",
                "TOOL_PYTHON_TIMEOUT_SECONDS": "20",
                "TOOL_MAX_ROUNDS": "3",
            }
            with patch.dict(os.environ, env, clear=True):
                policy = ToolPolicy.from_env(project_root=root)

        self.assertTrue(policy.tools_enabled)
        self.assertTrue(policy.allow_writes)
        self.assertTrue(policy.allow_python)
        self.assertFalse(policy.auto_approve_reads)
        self.assertFalse(policy.require_confirmation)
        self.assertTrue(policy.log_content)
        self.assertEqual(policy.read_root, root / "data")
        self.assertEqual(policy.write_root, root / "sandbox")
        self.assertEqual(policy.log_path, root / "audit" / "events.jsonl")
        self.assertEqual(policy.max_file_bytes, 2048)
        self.assertEqual(policy.max_output_bytes, 4096)
        self.assertEqual(policy.max_code_bytes, 8192)
        self.assertEqual(policy.max_list_entries, 50)
        self.assertEqual(policy.python_timeout_seconds, 20)
        self.assertEqual(policy.max_tool_rounds, 3)

    def test_invalid_environment_values_fall_back_or_clamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "TOOLS_ENABLED": "maybe",
                "TOOL_MAX_FILE_BYTES": "nope",
                "TOOL_MAX_OUTPUT_BYTES": "-1",
                "TOOL_MAX_CODE_BYTES": "999999",
                "TOOL_MAX_LIST_ENTRIES": "0",
                "TOOL_PYTHON_TIMEOUT_SECONDS": "0",
                "TOOL_MAX_ROUNDS": "99",
            }
            with patch.dict(os.environ, env, clear=True):
                policy = ToolPolicy.from_env(project_root=root)

        self.assertFalse(policy.tools_enabled)
        self.assertEqual(policy.max_file_bytes, 64 * 1024)
        self.assertEqual(policy.max_output_bytes, 1024)
        self.assertEqual(policy.max_code_bytes, 32 * 1024)
        self.assertEqual(policy.max_list_entries, 1)
        self.assertEqual(policy.python_timeout_seconds, 1)
        self.assertEqual(policy.max_tool_rounds, 4)

    def test_paths_outside_project_root_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / f"{root.name}_outside"
            env = {
                "TOOL_READ_ROOT": str(outside),
                "TOOL_WRITE_ROOT": str(outside),
                "TOOL_LOG_PATH": str(outside / "audit.jsonl"),
            }
            with patch.dict(os.environ, env, clear=True):
                policy = ToolPolicy.from_env(project_root=root)

        self.assertEqual(policy.read_root, root)
        self.assertEqual(policy.write_root, root / "workspace")
        self.assertEqual(
            policy.log_path, root / "logs" / "tool_calls.jsonl"
        )

    def test_relative_path_escape_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "TOOL_READ_ROOT": "..",
                "TOOL_WRITE_ROOT": "../outside",
                "TOOL_LOG_PATH": "../audit.jsonl",
            }
            with patch.dict(os.environ, env, clear=True):
                policy = ToolPolicy.from_env(project_root=root)

        self.assertEqual(policy.read_root, root)
        self.assertEqual(policy.write_root, root / "workspace")
        self.assertEqual(
            policy.log_path, root / "logs" / "tool_calls.jsonl"
        )

    def test_direct_construction_rejects_outside_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / f"{root.name}_outside"
            with self.assertRaises(ValueError):
                ToolPolicy(project_root=root, read_root=outside)
            with self.assertRaises(ValueError):
                ToolPolicy(project_root=root, write_root=outside)
            with self.assertRaises(ValueError):
                ToolPolicy(
                    project_root=root,
                    log_path=outside / "audit.jsonl",
                )

    def test_limits_are_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp),
                max_file_bytes=1,
                max_output_bytes=1,
                max_code_bytes=1,
                max_list_entries=0,
                python_timeout_seconds=0,
                max_tool_rounds=99,
            )
        self.assertEqual(policy.max_file_bytes, 1024)
        self.assertEqual(policy.max_output_bytes, 1024)
        self.assertEqual(policy.max_code_bytes, 1024)
        self.assertEqual(policy.max_list_entries, 1)
        self.assertEqual(policy.python_timeout_seconds, 1)
        self.assertEqual(policy.max_tool_rounds, 4)


class ToolPolicyDecisionTests(unittest.TestCase):
    def test_denies_everything_when_tools_are_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(project_root=Path(tmp))
        self.assertEqual(
            policy.denial_reason(_spec("read_file")),
            "Tools are disabled.",
        )

    def test_read_tool_is_allowed_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp), tools_enabled=True
            )
        self.assertIsNone(policy.denial_reason(_spec("read_file")))

    def test_write_tool_requires_write_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp), tools_enabled=True
            )
        self.assertEqual(
            policy.denial_reason(_spec("write_file", mutating=True)),
            "Write tools are disabled.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp),
                tools_enabled=True,
                allow_writes=True,
            )
        self.assertIsNone(
            policy.denial_reason(_spec("write_file", mutating=True))
        )

    def test_python_tool_requires_python_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp), tools_enabled=True
            )
        python_spec = _spec(
            "run_python", mutating=True, requires_python=True
        )
        self.assertEqual(
            policy.denial_reason(python_spec),
            "Python execution is disabled.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp),
                tools_enabled=True,
                allow_python=True,
            )
        self.assertIsNone(policy.denial_reason(python_spec))

    def test_approval_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = ToolPolicy(
                project_root=Path(tmp), tools_enabled=True
            )
        read_spec = _spec("read_file")
        sensitive_read = _spec("read_file", requires_approval=True)
        write_spec = _spec("write_file", mutating=True)
        python_spec = _spec(
            "run_python", mutating=True, requires_python=True
        )

        self.assertFalse(policy.approval_required(read_spec))
        self.assertTrue(policy.approval_required(sensitive_read))
        self.assertTrue(policy.approval_required(write_spec))
        self.assertTrue(policy.approval_required(python_spec))

        with tempfile.TemporaryDirectory() as tmp:
            no_auto_reads = ToolPolicy(
                project_root=Path(tmp),
                tools_enabled=True,
                auto_approve_reads=False,
            )
        self.assertTrue(no_auto_reads.approval_required(read_spec))

        with tempfile.TemporaryDirectory() as tmp:
            no_confirmation = ToolPolicy(
                project_root=Path(tmp),
                tools_enabled=True,
                require_confirmation=False,
            )
        self.assertFalse(no_confirmation.approval_required(write_spec))
        self.assertFalse(
            no_confirmation.approval_required(python_spec)
        )


if __name__ == "__main__":
    unittest.main()
