import tempfile
import unittest
from pathlib import Path

from assistant.tools import (
    AuditLogger,
    ToolExecutionError,
    ToolGateway,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)

from support import FakeTool, make_audit, make_policy, read_events


def _gateway(root: Path, tool=None, policy=None, approver=None):
    registry = ToolRegistry()
    if tool is not None:
        registry.register(tool)
    audit = make_audit(root)
    gateway = ToolGateway(
        policy or make_policy(root),
        registry,
        audit,
        approver=approver,
    )
    return gateway, audit


class ToolGatewayDenialTests(unittest.TestCase):
    def test_disabled_tools_are_denied_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("sample_tool")
            gateway, audit = _gateway(
                root,
                tool,
                policy=make_policy(root, tools_enabled=False),
            )

            result = gateway.dispatch("sample_tool", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Tools are disabled.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "disabled")

    def test_unknown_tool_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, audit = _gateway(root)

            result = gateway.dispatch("missing_tool", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Unknown tool.")
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "unknown_tool")

    def test_invalid_tool_name_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gateway, audit = _gateway(root)

            result = gateway.dispatch("", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Invalid tool name.")
            events = read_events(audit.path)
            self.assertEqual(events[0]["tool"], "<invalid>")
            self.assertEqual(events[0]["permission"], "invalid_arguments")

    def test_non_mapping_arguments_are_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("sample_tool")
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("sample_tool", ["not", "a", "mapping"])

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Tool arguments must be an object.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "invalid_arguments")
            self.assertEqual(events[0]["arguments"]["value_type"], "list")

    def test_validation_error_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool(
                "sample_tool",
                validation_error=ToolValidationError("bad arguments"),
            )
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("sample_tool", {"path": "x"})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Invalid tool arguments.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "invalid_arguments")
            self.assertEqual(
                events[0]["error_type"], "ToolValidationError"
            )

    def test_write_tool_is_denied_when_writes_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)
            gateway, audit = _gateway(
                root,
                tool,
                policy=make_policy(root, allow_writes=False),
            )

            result = gateway.dispatch("write_file", {"path": "notes.txt"})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Write tools are disabled.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "denied")

    def test_policy_denial_does_not_call_approver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)
            calls = []

            def approver(spec, arguments):
                calls.append((spec, arguments))
                return True

            gateway, _ = _gateway(
                root,
                tool,
                policy=make_policy(root, allow_writes=False),
                approver=approver,
            )

            result = gateway.dispatch("write_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(calls, [])
            self.assertEqual(tool.calls, 0)


class ToolGatewayApprovalTests(unittest.TestCase):
    def test_read_tool_executes_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file")
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("read_file", {"path": "notes.txt"})

            self.assertTrue(result.ok)
            self.assertEqual(result.output, "ok")
            self.assertEqual(tool.calls, 1)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "auto")

    def test_write_tool_executes_when_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)
            seen = []

            def approver(spec, arguments):
                seen.append((spec.name, dict(arguments)))
                return True

            gateway, audit = _gateway(root, tool, approver=approver)

            result = gateway.dispatch("write_file", {"path": "notes.txt"})

            self.assertTrue(result.ok)
            self.assertEqual(tool.calls, 1)
            self.assertEqual(seen, [("write_file", {"path": "notes.txt"})])
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "approved")

    def test_write_tool_is_denied_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)
            gateway, audit = _gateway(
                root, tool, approver=lambda spec, arguments: False
            )

            result = gateway.dispatch("write_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Permission denied.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "denied")

    def test_write_tool_denied_without_approver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)
            gateway, _ = _gateway(root, tool)

            result = gateway.dispatch("write_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(
                result.error,
                "Approval is required but no approver is configured.",
            )
            self.assertEqual(tool.calls, 0)

    def test_approval_exception_is_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("write_file", mutating=True)

            def approver(spec, arguments):
                raise RuntimeError("prompt failed")

            gateway, audit = _gateway(root, tool, approver=approver)

            result = gateway.dispatch("write_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Approval failed.")
            self.assertEqual(tool.calls, 0)
            events = read_events(audit.path)
            self.assertEqual(events[0]["error_type"], "RuntimeError")

    def test_python_tool_requires_python_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool(
                "run_python", mutating=True, requires_python=True
            )
            gateway, _ = _gateway(
                root,
                tool,
                policy=make_policy(
                    root, allow_python=False, allow_writes=True
                ),
                approver=lambda spec, arguments: True,
            )

            result = gateway.dispatch("run_python", {"code": "print(1)"})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Python execution is disabled.")
            self.assertEqual(tool.calls, 0)

    def test_python_tool_is_not_blocked_by_write_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool(
                "run_python", mutating=True, requires_python=True
            )
            gateway, audit = _gateway(
                root,
                tool,
                policy=make_policy(
                    root, allow_python=True, allow_writes=False
                ),
                approver=lambda spec, arguments: True,
            )

            result = gateway.dispatch("run_python", {"code": "print(1)"})

            self.assertTrue(result.ok)
            self.assertEqual(tool.calls, 1)
            events = read_events(audit.path)
            self.assertEqual(events[0]["permission"], "approved")


class ToolGatewayExecutionTests(unittest.TestCase):
    def test_execution_error_is_returned_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool(
                "read_file",
                execution_error=ToolExecutionError("boom"),
            )
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("read_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(result.error, "Tool execution failed.")
            self.assertEqual(tool.calls, 1)
            events = read_events(audit.path)
            self.assertEqual(
                events[0]["error_type"], "ToolExecutionError"
            )
            self.assertEqual(events[0]["error_detail"], "boom")

    def test_unexpected_execution_error_is_returned_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool(
                "read_file", execution_error=RuntimeError("unexpected")
            )
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("read_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(tool.calls, 1)
            events = read_events(audit.path)
            self.assertEqual(events[0]["error_type"], "RuntimeError")

    def test_invalid_tool_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file", result="not a ToolResult")
            gateway, audit = _gateway(root, tool)

            result = gateway.dispatch("read_file", {})

            self.assertFalse(result.ok)
            self.assertEqual(
                result.error, "Tool returned an invalid result."
            )
            events = read_events(audit.path)
            self.assertEqual(events[0]["error_type"], "TypeError")

    def test_request_id_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file")
            gateway, audit = _gateway(root, tool)

            gateway.dispatch("read_file", {}, request_id="req-123")

            event = read_events(audit.path)[0]
            self.assertEqual(event["request_id"], "req-123")

    def test_audit_failure_does_not_break_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file")
            registry = ToolRegistry()
            registry.register(tool)
            audit_path = root / "audit-as-directory"
            audit_path.mkdir()
            gateway = ToolGateway(
                make_policy(root),
                registry,
                AuditLogger(audit_path),
            )

            result = gateway.dispatch("read_file", {})

            self.assertTrue(result.ok)
            self.assertEqual(tool.calls, 1)


class ToolGatewayAuditPrivacyTests(unittest.TestCase):
    def test_sensitive_arguments_are_hashed_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file")
            gateway, audit = _gateway(root, tool)

            gateway.dispatch(
                "read_file",
                {
                    "path": "notes.txt",
                    "content": "super-secret",
                    "code": "print('secret')",
                },
            )

            log_text = audit.path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret", log_text)
            self.assertNotIn("print('secret')", log_text)
            event = read_events(audit.path)[0]
            self.assertIn("sha256", event["arguments"]["content"])
            self.assertIn("bytes", event["arguments"]["content"])
            self.assertIn("sha256", event["arguments"]["code"])

    def test_content_preview_requires_explicit_log_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = FakeTool("read_file")
            gateway, audit = _gateway(
                root,
                tool,
                policy=make_policy(root, log_content=True),
            )

            gateway.dispatch("read_file", {"content": "visible"})

            event = read_events(audit.path)[0]
            self.assertEqual(
                event["arguments"]["content"]["preview"], "visible"
            )

    def test_metadata_is_summarized_for_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = ToolResult.success(
                "read_file",
                "ok",
                {"content": "secret metadata", "bytes": 4},
            )
            tool = FakeTool("read_file", result=result)
            gateway, audit = _gateway(root, tool)

            gateway.dispatch("read_file", {})

            log_text = audit.path.read_text(encoding="utf-8")
            self.assertNotIn("secret metadata", log_text)
            event = read_events(audit.path)[0]
            self.assertIn("sha256", event["metadata"]["content"])


if __name__ == "__main__":
    unittest.main()
