import ctypes
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base import ToolResult, ToolSpec, ToolValidationError
from .policy import (
    DEFAULT_MAX_CODE_BYTES,
    DEFAULT_MAX_PYTHON_OUTPUT_BYTES,
    DEFAULT_PYTHON_CPU_TIME_SECONDS,
    DEFAULT_PYTHON_JOB_MEMORY_BYTES,
    DEFAULT_PYTHON_MAX_PROCESSES,
    DEFAULT_PYTHON_PROCESS_MEMORY_BYTES,
    DEFAULT_PYTHON_TIMEOUT_SECONDS,
    MAX_PYTHON_CPU_TIME_SECONDS,
    MAX_PYTHON_JOB_MEMORY_BYTES,
    MAX_PYTHON_MAX_PROCESSES,
    MAX_PYTHON_PROCESS_MEMORY_BYTES,
    MIN_PYTHON_CPU_TIME_SECONDS,
    MIN_PYTHON_JOB_MEMORY_BYTES,
    MIN_PYTHON_MAX_PROCESSES,
    MIN_PYTHON_PROCESS_MEMORY_BYTES,
    PROJECT_ROOT,
    ToolPolicy,
)

MAX_PYTHON_CODE_BYTES = DEFAULT_MAX_CODE_BYTES
MAX_PYTHON_TIMEOUT_SECONDS = DEFAULT_PYTHON_TIMEOUT_SECONDS
MAX_PYTHON_OUTPUT_BYTES = DEFAULT_MAX_PYTHON_OUTPUT_BYTES

_READ_CHUNK_SIZE = 4096
_MONITOR_INTERVAL_SECONDS = 0.05
_TERMINATION_TIMEOUT_SECONDS = 5.0
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_ASSIGN_RIGHTS = 0x0D01
_CREATE_SUSPENDED = 0x00000004
_SECONDS_TO_100NS = 10_000_000


class _JobObjectError(RuntimeError):
    pass


def _close_windows_handle(kernel32: Any, handle: int) -> None:
    try:
        closed = bool(kernel32.CloseHandle(handle))
    except OSError as exc:
        raise _JobObjectError("Could not close a Windows handle.") from exc
    if not closed:
        raise _JobObjectError("Could not close a Windows handle.")


_OUTPUT_LIMIT_MARKERS = (
    "[output limit exceeded; process terminated]",
    "[truncated]",
    "[...]",
    "...",
    "~",
)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _select_marker(candidates: tuple[str, ...], max_bytes: int) -> str:
    for candidate in candidates:
        if len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
    return ""


def _with_separator(marker: str, max_bytes: int) -> str:
    if marker and len(marker.encode("utf-8")) + 1 < max_bytes:
        return marker + "\n"
    return marker


class _OutputCapture:
    def __init__(self, limit: int) -> None:
        self.limit = max(0, int(limit))
        self._lock = threading.Lock()
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stdout_total = 0
        self._stderr_total = 0
        self._captured = 0
        self._limit_hit = threading.Event()

    @property
    def limit_hit(self) -> bool:
        return self._limit_hit.is_set()

    def feed(self, stream_name: str, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if stream_name == "stdout":
                self._stdout_total += len(data)
            else:
                self._stderr_total += len(data)

            remaining = self.limit - self._captured
            if remaining > 0:
                chunk = data[:remaining]
                if stream_name == "stdout":
                    self._stdout.extend(chunk)
                else:
                    self._stderr.extend(chunk)
                self._captured += len(chunk)
            if len(data) > remaining:
                self._limit_hit.set()

    def snapshot(self) -> tuple[bytes, bytes, int, int]:
        with self._lock:
            return (
                bytes(self._stdout),
                bytes(self._stderr),
                self._stdout_total,
                self._stderr_total,
            )


class _WindowsJob:
    def __init__(
        self,
        handle: int,
        kernel32: Any,
        *,
        process_memory_bytes: int,
        job_memory_bytes: int,
        cpu_time_seconds: int,
        max_processes: int,
    ) -> None:
        self.handle = handle
        self._kernel32 = kernel32
        self.process_memory_bytes = process_memory_bytes
        self.job_memory_bytes = job_memory_bytes
        self.cpu_time_seconds = cpu_time_seconds
        self.max_processes = max_processes

    def assign_and_resume(self, pid: int) -> tuple[bool, bool]:
        process_handle = self._kernel32.OpenProcess(
            _PROCESS_ASSIGN_RIGHTS, False, pid
        )
        if not process_handle:
            return False, False

        try:
            assigned = bool(
                self._kernel32.AssignProcessToJobObject(
                    self.handle, process_handle
                )
            )
            if not assigned:
                return False, False

            try:
                from ctypes import wintypes

                ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
                ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
                ntdll.NtResumeProcess.restype = ctypes.c_long
                resumed = ntdll.NtResumeProcess(process_handle) >= 0
            except (AttributeError, OSError):
                resumed = False
            return True, resumed
        finally:
            _close_windows_handle(self._kernel32, process_handle)

    def terminate(self) -> None:
        if self.handle:
            try:
                terminated = bool(
                    self._kernel32.TerminateJobObject(self.handle, 1)
                )
            except OSError as exc:
                raise _JobObjectError(
                    "Could not terminate the Windows Job Object."
                ) from exc
            if not terminated:
                raise _JobObjectError(
                    "Could not terminate the Windows Job Object."
                )

    def close(self) -> None:
        if self.handle:
            try:
                _close_windows_handle(self._kernel32, self.handle)
            except _JobObjectError as close_error:
                try:
                    self.terminate()
                except _JobObjectError as terminate_error:
                    raise close_error from terminate_error
                raise
            self.handle = 0


def _create_windows_job(
    *,
    process_memory_bytes: int,
    job_memory_bytes: int,
    cpu_time_seconds: int,
    max_processes: int,
) -> _WindowsJob | None:
    if os.name != "nt":
        return None
    if (
        process_memory_bytes <= 0
        or job_memory_bytes <= 0
        or cpu_time_seconds <= 0
        or max_processes <= 0
    ):
        raise _JobObjectError("Invalid Python resource limits.")
    try:
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                (
                    "BasicLimitInformation",
                    JOBOBJECT_BASIC_LIMIT_INFORMATION,
                ),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [
            wintypes.LPVOID,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _JobObjectError(
                "Could not create a Windows Job Object."
            )

        try:
            cpu_time_100ns = int(cpu_time_seconds) * _SECONDS_TO_100NS
            information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            information.BasicLimitInformation.PerProcessUserTimeLimit = (
                cpu_time_100ns
            )
            information.BasicLimitInformation.PerJobUserTimeLimit = (
                cpu_time_100ns
            )
            information.BasicLimitInformation.LimitFlags = (
                _JOB_OBJECT_LIMIT_PROCESS_TIME
                | _JOB_OBJECT_LIMIT_JOB_TIME
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_JOB_MEMORY
                | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            information.BasicLimitInformation.ActiveProcessLimit = int(
                max_processes
            )
            information.ProcessMemoryLimit = int(process_memory_bytes)
            information.JobMemoryLimit = int(job_memory_bytes)
            configured = kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
            )
            if not configured:
                raise _JobObjectError(
                    "Could not configure Windows Job Object resource limits."
                )

            return _WindowsJob(
                handle,
                kernel32,
                process_memory_bytes=process_memory_bytes,
                job_memory_bytes=job_memory_bytes,
                cpu_time_seconds=cpu_time_seconds,
                max_processes=max_processes,
            )
        except BaseException as exc:
            try:
                _close_windows_handle(kernel32, handle)
            except _JobObjectError as close_exc:
                raise close_exc from exc
            raise
    except _JobObjectError:
        raise
    except (AttributeError, OSError, ValueError) as exc:
        raise _JobObjectError(
            "Windows Job Object setup failed."
        ) from exc


def _windows_taskkill(pid: int) -> bool:
    try:
        completed = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_TERMINATION_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    job: _WindowsJob | None,
    job_assigned: bool,
) -> None:
    if process.poll() is not None:
        return

    termination_error: _JobObjectError | None = None
    fallback_kill = False
    if os.name == "nt":
        if job is not None and job_assigned:
            try:
                job.terminate()
            except _JobObjectError as exc:
                termination_error = exc
                fallback_kill = not _windows_taskkill(process.pid)
        else:
            fallback_kill = not _windows_taskkill(process.pid)
        if fallback_kill:
            try:
                process.kill()
            except OSError as exc:
                if termination_error is None:
                    termination_error = _JobObjectError(
                        "Could not terminate the Python process."
                    )
                    termination_error.__cause__ = exc
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass

    try:
        process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as wait_error:
        try:
            process.kill()
        except OSError as kill_error:
            raise _JobObjectError(
                "Could not terminate the Python process."
            ) from kill_error
        try:
            process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as final_wait_error:
            raise _JobObjectError(
                "Python process did not exit after termination."
            ) from (termination_error or final_wait_error or wait_error)
    if process.poll() is None:
        raise _JobObjectError(
            "Python process did not exit after termination."
        ) from termination_error
    if termination_error is not None:
        raise termination_error


def _cleanup_directory(path: Path) -> None:
    def onerror(function: Any, target: str, exc_info: Any) -> None:
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    for _ in range(2):
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onerror=onerror)
            return
        except OSError:
            time.sleep(0.05)


def _build_environment(run_dir: Path) -> dict[str, str]:
    environment = {
        "TEMP": str(run_dir),
        "TMP": str(run_dir),
        "TMPDIR": str(run_dir),
        "HOME": str(run_dir),
        "USERPROFILE": str(run_dir),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "LANG",
        "LC_ALL",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _read_stream(
    stream: Any, stream_name: str, capture: _OutputCapture
) -> None:
    try:
        while True:
            data = stream.read(_READ_CHUNK_SIZE)
            if not data:
                break
            capture.feed(stream_name, data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _combine_output(stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode("utf-8", "replace")
    stderr_text = stderr.decode("utf-8", "replace")
    if not stderr_text:
        return stdout_text
    if not stdout_text:
        return "[stderr]\n" + stderr_text
    return stdout_text + "\n[stderr]\n" + stderr_text


def _join_readers(
    threads: list[threading.Thread],
    process: subprocess.Popen[bytes],
) -> None:
    for thread in threads:
        thread.join(timeout=2.0)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    for thread in threads:
        thread.join(timeout=1.0)


class RunPythonTool:
    spec = ToolSpec(
        name="run_python",
        description=(
            "Run Python code in a subprocess after explicit user approval. "
            "Execution is limited in time and captured output, but it is NOT "
            "a security sandbox and can access files, the network, and other "
            "processes with the current user's permissions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python source code to execute. Maximum 8 KB of "
                        "UTF-8 encoded source."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_PYTHON_TIMEOUT_SECONDS,
                    "description": (
                        "Maximum execution time in seconds. Defaults to 10."
                    ),
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        mutating=True,
        requires_approval=True,
        requires_python=True,
    )

    def __init__(
        self,
        root: Path | str | ToolPolicy | None = None,
        *,
        policy: ToolPolicy | None = None,
        python_workdir: Path | str | None = None,
        max_code_bytes: int | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: int | None = None,
        process_memory_bytes: int | None = None,
        job_memory_bytes: int | None = None,
        cpu_time_seconds: int | None = None,
        max_processes: int | None = None,
    ) -> None:
        if isinstance(root, ToolPolicy):
            policy = root
            root = None
        if policy is not None:
            if python_workdir is None:
                python_workdir = policy.python_workdir
            if max_code_bytes is None:
                max_code_bytes = policy.max_code_bytes
            if max_output_bytes is None:
                max_output_bytes = policy.max_output_bytes
            if timeout_seconds is None:
                timeout_seconds = policy.python_timeout_seconds
            if process_memory_bytes is None:
                process_memory_bytes = policy.python_process_memory_bytes
            if job_memory_bytes is None:
                job_memory_bytes = policy.python_job_memory_bytes
            if cpu_time_seconds is None:
                cpu_time_seconds = policy.python_cpu_time_seconds
            if max_processes is None:
                max_processes = policy.python_max_processes
        elif python_workdir is None and root is not None:
            python_workdir = root

        self.workdir = Path(
            python_workdir or (PROJECT_ROOT / "workspace" / "python")
        ).resolve()
        self.max_code_bytes = max(
            1,
            min(
                MAX_PYTHON_CODE_BYTES,
                int(
                    max_code_bytes
                    if max_code_bytes is not None
                    else MAX_PYTHON_CODE_BYTES
                ),
            ),
        )
        self.max_output_bytes = max(
            0,
            min(
                MAX_PYTHON_OUTPUT_BYTES,
                int(
                    max_output_bytes
                    if max_output_bytes is not None
                    else MAX_PYTHON_OUTPUT_BYTES
                ),
            ),
        )
        self.timeout_seconds = max(
            1,
            min(
                MAX_PYTHON_TIMEOUT_SECONDS,
                int(
                    timeout_seconds
                    if timeout_seconds is not None
                    else MAX_PYTHON_TIMEOUT_SECONDS
                ),
            ),
        )
        self.process_memory_bytes = max(
            MIN_PYTHON_PROCESS_MEMORY_BYTES,
            min(
                MAX_PYTHON_PROCESS_MEMORY_BYTES,
                int(
                    process_memory_bytes
                    if process_memory_bytes is not None
                    else DEFAULT_PYTHON_PROCESS_MEMORY_BYTES
                ),
            ),
        )
        self.job_memory_bytes = max(
            MIN_PYTHON_JOB_MEMORY_BYTES,
            min(
                MAX_PYTHON_JOB_MEMORY_BYTES,
                int(
                    job_memory_bytes
                    if job_memory_bytes is not None
                    else DEFAULT_PYTHON_JOB_MEMORY_BYTES
                ),
            ),
        )
        self.cpu_time_seconds = max(
            MIN_PYTHON_CPU_TIME_SECONDS,
            min(
                MAX_PYTHON_CPU_TIME_SECONDS,
                int(
                    cpu_time_seconds
                    if cpu_time_seconds is not None
                    else DEFAULT_PYTHON_CPU_TIME_SECONDS
                ),
            ),
        )
        self.max_processes = max(
            MIN_PYTHON_MAX_PROCESSES,
            min(
                MAX_PYTHON_MAX_PROCESSES,
                int(
                    max_processes
                    if max_processes is not None
                    else DEFAULT_PYTHON_MAX_PROCESSES
                ),
            ),
        )

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("Tool arguments must be an object.")

        unknown = set(arguments) - {"code", "timeout_seconds"}
        if unknown:
            raise ToolValidationError(
                "Unknown argument(s): " + ", ".join(sorted(map(str, unknown)))
            )

        code = arguments.get("code")
        if not isinstance(code, str):
            raise ToolValidationError("'code' must be a string.")
        if not code.strip():
            raise ToolValidationError("'code' must not be empty.")
        if "\x00" in code:
            raise ToolValidationError("'code' must not contain NUL bytes.")
        try:
            encoded = code.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolValidationError(
                "'code' must be valid UTF-8 text."
            ) from exc
        if len(encoded) > self.max_code_bytes:
            raise ToolValidationError(
                f"Code is too large ({len(encoded)} bytes; "
                f"limit is {self.max_code_bytes})."
            )

        timeout = arguments.get(
            "timeout_seconds", self.timeout_seconds
        )
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise ToolValidationError(
                "'timeout_seconds' must be an integer."
            )
        if timeout < 1:
            raise ToolValidationError(
                "'timeout_seconds' must be at least 1."
            )
        timeout = min(timeout, self.timeout_seconds)

        return {
            "code": code,
            "timeout_seconds": timeout,
        }

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            validated = self.validate(arguments)
        except ToolValidationError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            return self._run(
                validated["code"],
                validated["timeout_seconds"],
            )
        except _JobObjectError:
            return ToolResult.failure(
                self.spec.name,
                "Could not safely manage the Windows Python process.",
            )

    def _run(self, code: str, timeout_seconds: int) -> ToolResult:
        run_dir: Path | None = None
        process: subprocess.Popen[bytes] | None = None
        job: _WindowsJob | None = None
        job_assigned = False
        capture = _OutputCapture(self.max_output_bytes)
        readers: list[threading.Thread] = []
        started = time.perf_counter()
        timed_out = False
        output_limit_hit = False
        stdin_failed = False

        try:
            try:
                self.workdir.mkdir(parents=True, exist_ok=True)
                if not self.workdir.is_dir():
                    return ToolResult.failure(
                        self.spec.name,
                        "Python working directory is not a directory.",
                    )
                run_dir = Path(
                    tempfile.mkdtemp(
                        prefix="run-", dir=str(self.workdir)
                    )
                )
            except OSError:
                return ToolResult.failure(
                    self.spec.name,
                    "Could not create a Python working directory.",
                )

            if os.name == "nt":
                try:
                    job = _create_windows_job(
                        process_memory_bytes=self.process_memory_bytes,
                        job_memory_bytes=self.job_memory_bytes,
                        cpu_time_seconds=self.cpu_time_seconds,
                        max_processes=self.max_processes,
                    )
                except _JobObjectError:
                    return ToolResult.failure(
                        self.spec.name,
                        "Could not apply Python resource limits.",
                    )
            if os.name == "nt" and job is None:
                return ToolResult.failure(
                    self.spec.name,
                    "Could not apply Python resource limits.",
                )
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                )
                creationflags |= _CREATE_SUSPENDED

            popen_kwargs: dict[str, Any] = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": str(run_dir),
                "env": _build_environment(run_dir),
                "shell": False,
                "close_fds": True,
                "bufsize": 0,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = creationflags
            else:
                popen_kwargs["start_new_session"] = True

            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-u",
                    "-X",
                    "utf8",
                    "-",
                ],
                **popen_kwargs,
            )

            if os.name == "nt" and job is not None:
                job_assigned, resumed = job.assign_and_resume(
                    process.pid
                )
                if not job_assigned:
                    return ToolResult.failure(
                        self.spec.name,
                        "Could not apply Python resource limits.",
                    )
                if not resumed:
                    return ToolResult.failure(
                        self.spec.name,
                        "Could not start the Python process.",
                    )

            readers = [
                threading.Thread(
                    target=_read_stream,
                    args=(process.stdout, "stdout", capture),
                    daemon=True,
                ),
                threading.Thread(
                    target=_read_stream,
                    args=(process.stderr, "stderr", capture),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()

            try:
                if process.stdin is None:
                    raise OSError("stdin is unavailable")
                process.stdin.write(code.encode("utf-8"))
                process.stdin.close()
            except (OSError, ValueError):
                stdin_failed = True

            deadline = time.monotonic() + timeout_seconds
            while True:
                if capture.limit_hit:
                    output_limit_hit = True
                    break
                if stdin_failed:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    process.wait(
                        timeout=min(_MONITOR_INTERVAL_SECONDS, remaining)
                    )
                    break
                except subprocess.TimeoutExpired:
                    continue

            if output_limit_hit or timed_out or stdin_failed:
                _terminate_process_tree(
                    process, job, job_assigned
                )

            _join_readers(readers, process)
            if capture.limit_hit:
                output_limit_hit = True
            stdout, stderr, stdout_total, stderr_total = (
                capture.snapshot()
            )
            duration_ms = round(
                (time.perf_counter() - started) * 1000, 2
            )

            if stdin_failed:
                return ToolResult.failure(
                    self.spec.name,
                    "Could not send code to Python.",
                    {
                        "exit_code": None,
                        "timed_out": False,
                        "output_limit_reached": False,
                        "truncated": False,
                        "stdout_bytes": len(stdout),
                        "stderr_bytes": len(stderr),
                        "stdout_total_bytes": stdout_total,
                        "stderr_total_bytes": stderr_total,
                        "duration_ms": duration_ms,
                    },
                )

            if timed_out:
                return ToolResult.failure(
                    self.spec.name,
                    "Python execution timed out.",
                    {
                        "exit_code": None,
                        "timed_out": True,
                        "output_limit_reached": False,
                        "truncated": False,
                        "stdout_bytes": len(stdout),
                        "stderr_bytes": len(stderr),
                        "stdout_total_bytes": stdout_total,
                        "stderr_total_bytes": stderr_total,
                        "duration_ms": duration_ms,
                    },
                )

            exit_code = (
                None if output_limit_hit else process.returncode
            )
            if output_limit_hit:
                marker = _select_marker(
                    _OUTPUT_LIMIT_MARKERS, self.max_output_bytes
                )
            elif exit_code not in (None, 0):
                marker = _select_marker(
                    (
                        f"[exit code: {exit_code}]",
                        f"[exit {exit_code}]",
                        f"[{exit_code}]",
                        "!",
                    ),
                    self.max_output_bytes,
                )
            else:
                marker = ""
            prefix = _with_separator(marker, self.max_output_bytes)

            combined = _combine_output(stdout, stderr)
            combined_bytes = combined.encode("utf-8")
            body_limit = max(
                0,
                self.max_output_bytes
                - len(prefix.encode("utf-8")),
            )
            body = _truncate_utf8(combined, body_limit)
            truncated = output_limit_hit or (
                len(combined_bytes) > body_limit
            )
            output = prefix + body

            return ToolResult.success(
                self.spec.name,
                output,
                {
                    "exit_code": exit_code,
                    "timed_out": False,
                    "output_limit_reached": output_limit_hit,
                    "truncated": truncated,
                    "stdout_bytes": len(stdout),
                    "stderr_bytes": len(stderr),
                    "stdout_total_bytes": stdout_total,
                    "stderr_total_bytes": stderr_total,
                    "duration_ms": duration_ms,
                },
            )
        except KeyboardInterrupt:
            raise
        except Exception:
            return ToolResult.failure(
                self.spec.name, "Python execution failed."
            )
        finally:
            cleanup_error: _JobObjectError | None = None
            if process is not None and process.poll() is None:
                try:
                    _terminate_process_tree(
                        process, job, job_assigned
                    )
                except _JobObjectError as exc:
                    cleanup_error = exc
            if process is not None:
                _join_readers(readers, process)
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except (OSError, ValueError):
                        pass
            if job is not None:
                try:
                    job.close()
                except _JobObjectError as exc:
                    cleanup_error = cleanup_error or exc
            if run_dir is not None:
                _cleanup_directory(run_dir)
            if cleanup_error is not None:
                raise cleanup_error


RunPython = RunPythonTool


__all__ = [
    "MAX_PYTHON_CODE_BYTES",
    "MAX_PYTHON_OUTPUT_BYTES",
    "MAX_PYTHON_TIMEOUT_SECONDS",
    "RunPython",
    "RunPythonTool",
]
