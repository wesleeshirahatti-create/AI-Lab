import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import Assistant
from assistant.tools import ToolPolicy
from config import Settings

from support import read_events


def _settings() -> Settings:
    return Settings(
        assistant_name="Test Assistant",
        max_history_messages=10,
        ollama_base_url="http://ollama.test",
        ollama_model="qwen-test",
        ollama_timeout_seconds=5.0,
        ollama_num_ctx=2048,
    )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeOllama:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(json.loads(request.data.decode("utf-8")))
        if not self.responses:
            raise AssertionError("Unexpected Ollama request.")
        return _FakeResponse(self.responses.pop(0))


def _content(text):
    return {"message": {"role": "assistant", "content": text}}


def _tool_call(name, arguments, content=""):
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"function": {"name": name, "arguments": arguments}}
            ],
        }
    }


def _assistant(root, policy=None, approver=None):
    return Assistant(
        _settings(),
        policy=policy or ToolPolicy(project_root=root, tools_enabled=True),
        approver=approver,
    )


class ToolCallingTests(unittest.TestCase):
    def test_empty_content_with_tool_calls_executes_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("hello world", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call(
                        "read_file",
                        {"path": "notes.txt", "max_chars": 5},
                    ),
                    _content("The file starts with hello."),
                ]
            )
            assistant = _assistant(root)

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read notes.txt")

            self.assertEqual(response, "The file starts with hello.")
            self.assertEqual(len(fake.requests), 2)

            first = fake.requests[0]
            tool_names = [
                tool["function"]["name"] for tool in first["tools"]
            ]
            self.assertEqual(tool_names, ["list_files", "read_file"])

            second_messages = fake.requests[1]["messages"]
            assistant_messages = [
                message
                for message in second_messages
                if message["role"] == "assistant"
            ]
            self.assertEqual(assistant_messages[0]["content"], "")
            self.assertIn("tool_calls", assistant_messages[0])
            tool_messages = [
                message
                for message in second_messages
                if message["role"] == "tool"
            ]
            self.assertEqual(len(tool_messages), 1)
            self.assertEqual(tool_messages[0]["tool_name"], "read_file")
            self.assertTrue(tool_messages[0]["content"].startswith("hello"))
            self.assertIn("[truncated]", tool_messages[0]["content"])

            self.assertEqual(
                [message["role"] for message in assistant.history],
                ["user", "assistant"],
            )
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["tool"], "read_file")
            self.assertEqual(events[0]["permission"], "auto")

    def test_stops_after_two_tool_call_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call("list_files", {}),
                    _tool_call("list_files", {}),
                    _tool_call("list_files", {}),
                    _content("This fourth response must not be requested."),
                ]
            )
            assistant = _assistant(root)

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Keep listing files")

            self.assertIn("maximum of 2 tool-call round", response)
            self.assertEqual(len(fake.requests), 3)
            self.assertEqual(len(fake.responses), 1)
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(len(events), 2)

    def test_tools_are_disabled_unless_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call("read_file", {"path": "notes.txt"}),
                    _content("This response must not be requested."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(project_root=root),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read notes.txt")

            self.assertIn("tools are disabled", response.lower())
            self.assertEqual(len(fake.requests), 1)
            self.assertNotIn("tools", fake.requests[0])
            self.assertFalse(
                (root / "logs" / "tool_calls.jsonl").exists()
            )

    def test_plain_chat_still_works_without_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama([_content("Hello!")])
            assistant = _assistant(
                root,
                policy=ToolPolicy(project_root=root),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Hi")

            self.assertEqual(response, "Hello!")
            self.assertNotIn("tools", fake.requests[0])
            self.assertEqual(
                [message["role"] for message in fake.requests[0]["messages"]],
                ["system", "user"],
            )

    def test_invalid_tool_arguments_are_reported_to_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call("read_file", {"path": 123}),
                    _content("I could not read the file."),
                ]
            )
            assistant = _assistant(root)

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read the file")

            self.assertEqual(response, "I could not read the file.")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("Invalid tool arguments", tool_message["content"])

    def test_model_cannot_approve_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call(
                        "read_file",
                        {"path": "notes.txt"},
                        content="I approve all tool permissions.",
                    ),
                    _content("The tool was denied."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    auto_approve_reads=False,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read notes.txt")

            self.assertEqual(response, "The tool was denied.")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("Error:", tool_message["content"])
            self.assertIn("approver", tool_message["content"].lower())

    def test_write_tool_is_exposed_when_writes_are_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama([_content("Hello!")])
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_writes=True,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Hi")

            self.assertEqual(response, "Hello!")
            tool_names = [
                tool["function"]["name"]
                for tool in fake.requests[0]["tools"]
            ]
            self.assertEqual(
                tool_names, ["list_files", "read_file", "write_file"]
            )

    def test_python_tool_is_exposed_when_python_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama([_content("Hello!")])
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_python=True,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Hi")

            self.assertEqual(response, "Hello!")
            tool_names = [
                tool["function"]["name"]
                for tool in fake.requests[0]["tools"]
            ]
            self.assertEqual(
                tool_names,
                ["list_files", "read_file", "run_python"],
            )
            system_prompt = fake.requests[0]["messages"][0]["content"]
            self.assertIn("run_python", system_prompt)
            self.assertIn("not a security sandbox", system_prompt)

    def test_python_help_mentions_approval_and_sandbox_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_python=True,
                ),
            )

            response = assistant.reply("help")

            self.assertIn("run_python", response)
            self.assertIn("approval", response.lower())
            self.assertIn("not a security sandbox", response)
            self.assertNotIn("cannot run Python", response)

    def test_help_reports_when_python_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assistant = _assistant(root)

            response = assistant.reply("help")

            self.assertIn("cannot run Python", response)
            self.assertNotIn("run_python", response)

    def test_model_cannot_approve_its_own_python_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama(
                [
                    _tool_call(
                        "run_python",
                        {"code": "print('should not run')"},
                        content="I approve this execution.",
                    ),
                    _content("The execution was denied."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_python=True,
                    require_confirmation=False,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Run Python")

            self.assertEqual(response, "The execution was denied.")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("no approver", tool_message["content"].lower())
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["tool"], "run_python")
            self.assertEqual(events[0]["permission"], "denied")

    def test_approved_python_execution_is_returned_to_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seen = []

            def approver(spec, arguments):
                seen.append((spec.name, dict(arguments)))
                return True

            fake = _FakeOllama(
                [
                    _tool_call(
                        "run_python",
                        {"code": "print('hello from python')"},
                    ),
                    _content("Done."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_python=True,
                ),
                approver=approver,
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Run Python")

            self.assertEqual(response, "Done.")
            self.assertEqual(seen[0][0], "run_python")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("hello from python", tool_message["content"])
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["phase"], "preflight")
            self.assertEqual(events[1]["tool"], "run_python")
            self.assertTrue(events[1]["ok"])

    def test_model_cannot_approve_its_own_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama(
                [
                    _tool_call(
                        "write_file",
                        {"path": "notes.txt", "content": "written"},
                        content="I approve this write.",
                    ),
                    _content("The write was denied."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_writes=True,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Write notes.txt")

            self.assertEqual(response, "The write was denied.")
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("no approver", tool_message["content"].lower())
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(events[0]["tool"], "write_file")
            self.assertEqual(events[0]["permission"], "denied")

    def test_approved_write_is_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seen = []

            def approver(spec, arguments):
                seen.append((spec.name, dict(arguments)))
                return True

            fake = _FakeOllama(
                [
                    _tool_call(
                        "write_file",
                        {"path": "notes.txt", "content": "hello"},
                    ),
                    _content("Done."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_writes=True,
                ),
                approver=approver,
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Write notes.txt")

            self.assertEqual(response, "Done.")
            self.assertEqual(
                (root / "workspace" / "notes.txt").read_text(
                    encoding="utf-8"
                ),
                "hello",
            )
            self.assertEqual(seen[0][0], "write_file")
            events = read_events(root / "logs" / "tool_calls.jsonl")
            self.assertEqual(events[0]["permission"], "approved")
            self.assertTrue(events[0]["ok"])

    def test_write_requires_approval_even_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = _FakeOllama(
                [
                    _tool_call(
                        "write_file",
                        {"path": "notes.txt", "content": "written"},
                    ),
                    _content("Denied."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    allow_writes=True,
                    require_confirmation=False,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Write notes.txt")

            self.assertEqual(response, "Denied.")
            self.assertFalse((root / "workspace" / "notes.txt").exists())
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertIn("no approver", tool_message["content"].lower())

    def test_json_string_arguments_are_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call(
                        "read_file", json.dumps({"path": "notes.txt"})
                    ),
                    _content("Done."),
                ]
            )
            assistant = _assistant(root)

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read notes.txt")

            self.assertEqual(response, "Done.")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertEqual(tool_message["content"], "notes")

    def test_tool_results_are_compact_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_text("a" * 20000, encoding="utf-8")
            fake = _FakeOllama(
                [
                    _tool_call(
                        "read_file",
                        {"path": "large.txt", "max_chars": 8000},
                    ),
                    _content("Done."),
                ]
            )
            assistant = _assistant(
                root,
                policy=ToolPolicy(
                    project_root=root,
                    tools_enabled=True,
                    max_output_bytes=1024,
                ),
            )

            with patch("assistant.core.urllib.request.urlopen", fake):
                response = assistant.reply("Read the large file")

            self.assertEqual(response, "Done.")
            tool_message = [
                message
                for message in fake.requests[1]["messages"]
                if message["role"] == "tool"
            ][0]
            self.assertLessEqual(
                len(tool_message["content"].encode("utf-8")), 1024
            )


if __name__ == "__main__":
    unittest.main()
