import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.tools import (
    MAX_PYTHON_CODE_BYTES,
    MAX_PYTHON_OUTPUT_BYTES,
    MAX_PYTHON_TIMEOUT_SECONDS,
    RunPythonTool,
    ToolValidationError,
)
from assistant.tools.python_exec import (
    _CREATE_SUSPENDED,
    _JobObjectError,
    _WindowsJob,
)


def _tool(tmp, **overrides):
    return RunPythonTool(Path(tmp) / "python", **overrides)


def _pid_exists(pid):
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_pid_exit(pid, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.05)
    return not _pid_exists(pid)


class _FakeKernel32:
    def __init__(
        self,
        events,
        *,
        assign_result=True,
        terminate_result=True,
        close_results=None,
    ):
        self.events = events
        self.assign_result = assign_result
        self.terminate_result = terminate_result
        self.close_results = close_results or {}

    def OpenProcess(self, *_args):
        self.events.append("open_process")
        return 202

    def AssignProcessToJobObject(self, *_args):
        self.events.append("assign")
        return self.assign_result

    def TerminateJobObject(self, *_args):
        self.events.append("terminate_job")
        return self.terminate_result

    def CloseHandle(self, handle):
        self.events.append(("close_handle", handle))
        return self.close_results.get(handle, True)


class _RecordingStdin(io.BytesIO):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self._closed_value = None

    def write(self, data):
        self.events.append("write_code")
        return super().write(data)

    def close(self):
        self._closed_value = super().getvalue()
        super().close()

    def getvalue(self):
        if self._closed_value is not None:
            return self._closed_value
        return super().getvalue()


class _FakeProcess:
    pid = 12345

    def __init__(self, events):
        self.events = events
        self.stdin = _RecordingStdin(events)
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.events.append("wait")
        if self.returncode is not None:
            return self.returncode
        if self.stdin.getvalue():
            self.returncode = 0
            return self.returncode
        raise subprocess.TimeoutExpired("python", timeout)

    def kill(self):
        self.events.append("kill_process")
        self.returncode = 1


@unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
class WindowsJobObjectTests(unittest.TestCase):
    def _job(self, events, **kwargs):
        kernel32 = _FakeKernel32(events, **kwargs)
        return _WindowsJob(
            101,
            kernel32,
            process_memory_bytes=1024,
            job_memory_bytes=2048,
            cpu_time_seconds=1,
            max_processes=2,
        )

    def _run_with_fake_process(self, tmp, job, events, resume_status=0):
        process = _FakeProcess(events)
        ntdll = Mock()
        ntdll.NtResumeProcess = Mock(
            side_effect=lambda _handle: events.append("resume")
            or resume_status
        )

        def popen(*_args, **kwargs):
            events.append(("create_process", kwargs["creationflags"]))
            return process

        def create_job(**_kwargs):
            events.append("configure_job")
            return job

        with (
            patch(
                "assistant.tools.python_exec._create_windows_job",
                side_effect=create_job,
            ),
            patch("assistant.tools.python_exec.subprocess.Popen", side_effect=popen),
            patch("assistant.tools.python_exec.ctypes.WinDLL", return_value=ntdll),
            patch(
                "assistant.tools.python_exec._windows_taskkill",
                return_value=False,
            ),
        ):
            result = _tool(tmp).execute({"code": "write_marker()"})

        return result, process, ntdll

    def test_job_limits_are_set_before_process_creation(self):
        events = []
        job_info = []
        kernel32 = Mock()
        kernel32.CreateJobObjectW.side_effect = (
            lambda *_args: events.append("create_job") or 101
        )

        def set_job_info(_handle, info_class, info_pointer, size):
            events.append("set_job_info")
            job_info.append((info_class, info_pointer._obj, size))
            return True

        kernel32.SetInformationJobObject.side_effect = set_job_info
        kernel32.OpenProcess.side_effect = (
            lambda *_args: events.append("open_process") or 202
        )
        kernel32.AssignProcessToJobObject.side_effect = (
            lambda *_args: events.append("assign") or True
        )
        kernel32.TerminateJobObject.return_value = True
        kernel32.CloseHandle.return_value = True

        ntdll = Mock()
        ntdll.NtResumeProcess = Mock(
            side_effect=lambda _handle: events.append("resume") or 0
        )

        def load_library(name, **_kwargs):
            return kernel32 if name == "kernel32" else ntdll

        process = _FakeProcess(events)

        def popen(*_args, **_kwargs):
            events.append("create_process")
            return process

        process_memory_bytes = 64 * 1024 * 1024
        job_memory_bytes = 128 * 1024 * 1024
        cpu_time_seconds = 7
        max_processes = 3

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "assistant.tools.python_exec.ctypes.WinDLL",
                    side_effect=load_library,
                ),
                patch(
                    "assistant.tools.python_exec.subprocess.Popen",
                    side_effect=popen,
                ),
            ):
                result = _tool(
                    tmp,
                    process_memory_bytes=process_memory_bytes,
                    job_memory_bytes=job_memory_bytes,
                    cpu_time_seconds=cpu_time_seconds,
                    max_processes=max_processes,
                ).execute({"code": "print('limited')"})

        self.assertTrue(result.ok)
        self.assertEqual(len(job_info), 1)
        info_class, information, information_size = job_info[0]
        self.assertEqual(info_class, 9)
        self.assertEqual(information_size, ctypes.sizeof(information))
        self.assertEqual(
            information.BasicLimitInformation.LimitFlags,
            0x0000230E,
        )
        self.assertEqual(
            information.BasicLimitInformation.PerProcessUserTimeLimit,
            cpu_time_seconds * 10_000_000,
        )
        self.assertEqual(
            information.BasicLimitInformation.PerJobUserTimeLimit,
            cpu_time_seconds * 10_000_000,
        )
        self.assertEqual(
            information.BasicLimitInformation.ActiveProcessLimit,
            max_processes,
        )
        self.assertEqual(
            information.ProcessMemoryLimit, process_memory_bytes
        )
        self.assertEqual(information.JobMemoryLimit, job_memory_bytes)
        self.assertLess(
            events.index("set_job_info"), events.index("create_process")
        )
        self.assertLess(events.index("assign"), events.index("resume"))
        self.assertLess(events.index("resume"), events.index("write_code"))

    def test_launch_is_suspended_and_limits_precede_code(self):
        events = []
        job = self._job(events)

        with tempfile.TemporaryDirectory() as tmp:
            result, _process, _ntdll = self._run_with_fake_process(
                tmp, job, events
            )

        self.assertTrue(result.ok)
        create_index = next(
            index
            for index, event in enumerate(events)
            if isinstance(event, tuple) and event[0] == "create_process"
        )
        self.assertLess(events.index("configure_job"), create_index)
        self.assertTrue(
            events[create_index][1] & _CREATE_SUSPENDED
        )
        self.assertLess(events.index("assign"), events.index("resume"))
        self.assertLess(events.index("resume"), events.index("write_code"))

    def test_assignment_failure_never_resumes_or_writes_code(self):
        events = []
        job = self._job(events, assign_result=False)

        with tempfile.TemporaryDirectory() as tmp:
            result, process, ntdll = self._run_with_fake_process(
                tmp, job, events
            )

        self.assertFalse(result.ok)
        ntdll.NtResumeProcess.assert_not_called()
        self.assertNotIn("write_code", events)
        self.assertEqual(process.stdin.getvalue(), b"")
        self.assertIn("kill_process", events)

    def test_assignment_failure_does_not_execute_submitted_code(self):
        events = []
        job = self._job(events, assign_result=False)
        ntdll = Mock()
        ntdll.NtResumeProcess = Mock(return_value=0)

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "executed.txt"
            code = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
            )
            with (
                patch(
                    "assistant.tools.python_exec._create_windows_job",
                    return_value=job,
                ),
                patch(
                    "assistant.tools.python_exec.ctypes.WinDLL",
                    return_value=ntdll,
                ),
                patch(
                    "assistant.tools.python_exec._windows_taskkill",
                    return_value=False,
                ),
            ):
                result = _tool(tmp).execute({"code": code})

            self.assertFalse(result.ok)
            self.assertFalse(marker.exists())
            ntdll.NtResumeProcess.assert_not_called()

    def test_resume_failure_never_writes_code(self):
        events = []
        job = self._job(events)

        with tempfile.TemporaryDirectory() as tmp:
            result, process, ntdll = self._run_with_fake_process(
                tmp, job, events, resume_status=-1
            )

        self.assertFalse(result.ok)
        ntdll.NtResumeProcess.assert_called_once()
        self.assertNotIn("write_code", events)
        self.assertEqual(process.stdin.getvalue(), b"")
        self.assertIn("terminate_job", events)

    def test_termination_and_handle_close_failures_are_reported(self):
        events = []
        terminate_job = self._job(events, terminate_result=False)
        with self.assertRaises(_JobObjectError):
            terminate_job.terminate()

        close_failure = self._job(
            events, close_results={101: False}
        )
        with self.assertRaises(_JobObjectError):
            close_failure.close()
        self.assertEqual(close_failure.handle, 101)
        self.assertIn("terminate_job", events)

        process_handle_close_failure = self._job(
            events, close_results={202: False}
        )
        ntdll = Mock()
        ntdll.NtResumeProcess = Mock(return_value=0)
        with patch(
            "assistant.tools.python_exec.ctypes.WinDLL",
            return_value=ntdll,
        ):
            with self.assertRaises(_JobObjectError):
                process_handle_close_failure.assign_and_resume(12345)


class RunPythonToolValidationTests(unittest.TestCase):
    def test_spec_has_required_flags_and_limits(self):
        spec = RunPythonTool.spec
        self.assertEqual(spec.name, "run_python")
        self.assertTrue(spec.mutating)
        self.assertTrue(spec.requires_approval)
        self.assertTrue(spec.requires_python)
        self.assertEqual(MAX_PYTHON_CODE_BYTES, 8 * 1024)
        self.assertEqual(MAX_PYTHON_OUTPUT_BYTES, 8 * 1024)
        self.assertEqual(MAX_PYTHON_TIMEOUT_SECONDS, 10)
        self.assertEqual(
            spec.parameters["properties"]["timeout_seconds"]["maximum"],
            10,
        )

    def test_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            invalid = [
                None,
                {},
                {"code": 123},
                {"code": ""},
                {"code": "   "},
                {"code": "x\x00"},
                {"code": "\ud800"},
                {"code": "x", "timeout_seconds": True},
                {"code": "x", "timeout_seconds": 0},
                {"code": "x", "timeout_seconds": "5"},
                {"code": "x", "extra": True},
                {"code": "x" * (MAX_PYTHON_CODE_BYTES + 1)},
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(ToolValidationError):
                        tool.validate(arguments)

    def test_accepts_exact_code_limit_and_clamps_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp, timeout_seconds=5)
            validated = tool.validate(
                {
                    "code": "x" * MAX_PYTHON_CODE_BYTES,
                    "timeout_seconds": 99,
                }
            )

        self.assertEqual(
            len(validated["code"].encode("utf-8")),
            MAX_PYTHON_CODE_BYTES,
        )
        self.assertEqual(validated["timeout_seconds"], 5)

    def test_execute_returns_failure_for_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute({"code": 123})

        self.assertFalse(result.ok)
        self.assertIn("code", result.error.lower())

    def test_workdir_creation_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "python"
            workdir.write_text("not a directory", encoding="utf-8")
            tool = RunPythonTool(workdir)

            result = tool.execute({"code": "print(1)"})

        self.assertFalse(result.ok)
        self.assertIn("working directory", result.error.lower())


class RunPythonToolExecutionTests(unittest.TestCase):
    def test_runs_code_and_captures_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute({"code": "print('hello')"})

            self.assertTrue(result.ok)
            self.assertIn("hello", result.output)
            self.assertEqual(result.metadata["exit_code"], 0)
            self.assertFalse(result.metadata["timed_out"])
            self.assertFalse(result.metadata["output_limit_reached"])
            self.assertFalse(result.metadata["truncated"])
            self.assertGreater(result.metadata["duration_ms"], 0)
            self.assertEqual(list(tool.workdir.iterdir()), [])

    def test_combines_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute(
                {
                    "code": (
                        "import sys\n"
                        "print('out')\n"
                        "print('err', file=sys.stderr)\n"
                    )
                }
            )

        self.assertTrue(result.ok)
        self.assertIn("out", result.output)
        self.assertIn("[stderr]", result.output)
        self.assertIn("err", result.output)
        self.assertEqual(result.metadata["stdout_total_bytes"], 5)
        self.assertGreater(result.metadata["stderr_total_bytes"], 0)

    def test_utf8_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute({"code": "print('h\u00e9llo')"})

        self.assertTrue(result.ok)
        self.assertIn("h\u00e9llo", result.output)

    def test_nonzero_exit_is_success_with_exit_code_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute({"code": "raise SystemExit(3)"})

        self.assertTrue(result.ok)
        self.assertIn("[exit code: 3]", result.output)
        self.assertEqual(result.metadata["exit_code"], 3)
        self.assertFalse(result.metadata["timed_out"])

    def test_timeout_terminates_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute(
                {
                    "code": "import time; time.sleep(10)",
                    "timeout_seconds": 1,
                }
            )

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.error.lower())
        self.assertTrue(result.metadata["timed_out"])
        self.assertFalse(result.metadata["output_limit_reached"])
        self.assertLess(result.metadata["duration_ms"], 5000)

    def test_output_limit_terminates_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp, max_output_bytes=256)
            result = tool.execute(
                {
                    "code": "while True: print('x' * 1024)",
                    "timeout_seconds": 5,
                }
            )

        self.assertTrue(result.ok)
        self.assertIn(
            "[output limit exceeded; process terminated]",
            result.output,
        )
        self.assertTrue(result.metadata["output_limit_reached"])
        self.assertTrue(result.metadata["truncated"])
        self.assertLessEqual(
            len(result.output.encode("utf-8")), 256
        )
        self.assertLess(result.metadata["duration_ms"], 5000)

    def test_output_exactly_at_limit_is_not_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp, max_output_bytes=64)
            result = tool.execute(
                {"code": "import sys; sys.stdout.write('x' * 64)"}
            )

        self.assertTrue(result.ok)
        self.assertEqual(len(result.output.encode("utf-8")), 64)
        self.assertFalse(result.metadata["output_limit_reached"])
        self.assertFalse(result.metadata["truncated"])

    def test_output_limit_invariant_for_small_limits(self):
        limits = (0, 1, 10, 43, 44, 256, 1024, MAX_PYTHON_OUTPUT_BYTES)
        with tempfile.TemporaryDirectory() as tmp:
            for limit in limits:
                with self.subTest(limit=limit):
                    tool = _tool(tmp, max_output_bytes=limit)
                    result = tool.execute(
                        {
                            "code": (
                                "import sys; "
                                f"sys.stdout.write('x' * {limit + 1})"
                            ),
                            "timeout_seconds": 5,
                        }
                    )
                    output = result.output.encode("utf-8")

                    self.assertTrue(result.ok)
                    self.assertLessEqual(len(output), limit)
                    self.assertTrue(
                        result.metadata["output_limit_reached"]
                    )
                    self.assertTrue(result.metadata["truncated"])
                    if limit == 0:
                        self.assertEqual(result.output, "")
                    elif limit == 1:
                        self.assertEqual(result.output, "~")
                    elif limit < 5:
                        self.assertTrue(result.output.startswith("..."))
                    elif limit < 11:
                        self.assertTrue(
                            result.output.startswith("[...]")
                        )
                    elif limit < 43:
                        self.assertTrue(
                            result.output.startswith("[truncated]")
                        )
                    else:
                        self.assertIn(
                            "[output limit exceeded; "
                            "process terminated]",
                            result.output,
                        )

    def test_exit_marker_never_exceeds_limit(self):
        limits = (0, 1, 10, 43, 44)
        with tempfile.TemporaryDirectory() as tmp:
            for limit in limits:
                with self.subTest(limit=limit):
                    tool = _tool(tmp, max_output_bytes=limit)
                    result = tool.execute(
                        {"code": "raise SystemExit(3)"}
                    )
                    output = result.output.encode("utf-8")

                    self.assertTrue(result.ok)
                    self.assertLessEqual(len(output), limit)
                    if limit == 0:
                        self.assertEqual(result.output, "")
                    elif limit >= 14:
                        self.assertIn("[exit code: 3]", result.output)
                    elif limit >= 8:
                        self.assertIn("[exit 3]", result.output)
                    elif limit >= 3:
                        self.assertIn("[3]", result.output)
                    else:
                        self.assertIn("!", result.output)

    def test_working_directory_is_temporary_and_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "cwd.txt"
            tool = _tool(tmp)
            code = (
                "import pathlib\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "str(pathlib.Path.cwd()))\n"
            )
            result = tool.execute({"code": code})
            run_cwd = Path(marker.read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            self.assertEqual(run_cwd.parent, tool.workdir)
            self.assertTrue(run_cwd.name.startswith("run-"))
            self.assertFalse(run_cwd.exists())

    def test_environment_is_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "env.json"
            tool = _tool(tmp)
            code = (
                "import json, os, pathlib\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "json.dumps({\n"
                "    'cwd': str(pathlib.Path.cwd()),\n"
                "    'home': os.environ.get('HOME'),\n"
                "    'temp': os.environ.get('TEMP'),\n"
                "    'tmp': os.environ.get('TMP'),\n"
                "    'tmpdir': os.environ.get('TMPDIR'),\n"
                "    'userprofile': os.environ.get('USERPROFILE'),\n"
                "    'pythonpath': os.environ.get('PYTHONPATH'),\n"
                "    'leak': os.environ.get('ASSISTANT_TEST_LEAK'),\n"
                "}))\n"
            )
            with patch.dict(
                os.environ,
                {
                    "ASSISTANT_TEST_LEAK": "should-not-leak",
                    "PYTHONPATH": "should-not-leak",
                },
            ):
                result = tool.execute({"code": code})
            values = json.loads(marker.read_text(encoding="utf-8"))

            self.assertTrue(result.ok)
            cwd = Path(values["cwd"])
            self.assertEqual(cwd.parent, tool.workdir)
            self.assertTrue(cwd.name.startswith("run-"))
            self.assertEqual(Path(values["home"]), cwd)
            self.assertEqual(Path(values["temp"]), cwd)
            self.assertEqual(Path(values["tmp"]), cwd)
            self.assertEqual(Path(values["tmpdir"]), cwd)
            self.assertEqual(Path(values["userprofile"]), cwd)
            self.assertIsNone(values["pythonpath"])
            self.assertIsNone(values["leak"])

    def test_source_is_not_passed_in_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = _tool(tmp)
            result = tool.execute(
                {"code": "import sys; print(repr(sys.argv))"}
            )

        self.assertTrue(result.ok)
        self.assertIn("['-']", result.output)
        self.assertNotIn("import sys", result.output)

    def test_process_tree_is_terminated_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            tool = _tool(tmp)
            code = (
                "import pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen(["
                f"{sys.executable!r}, '-c', "
                "'import time; time.sleep(10)'"
                "], stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL)\n"
                f"pathlib.Path({str(marker)!r}).write_text("
                "str(child.pid))\n"
                "time.sleep(10)\n"
            )

            result = tool.execute(
                {"code": code, "timeout_seconds": 2}
            )
            pid = int(marker.read_text(encoding="utf-8"))

            self.assertFalse(result.ok)
            self.assertTrue(result.metadata["timed_out"])
            self.assertTrue(_wait_for_pid_exit(pid))


if __name__ == "__main__":
    unittest.main()
