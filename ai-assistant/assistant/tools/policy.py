import os
from dataclasses import dataclass
from pathlib import Path

from .base import ToolSpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MAX_FILE_BYTES = 64 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024
DEFAULT_MAX_PYTHON_OUTPUT_BYTES = 8 * 1024
DEFAULT_MAX_CODE_BYTES = 8 * 1024
DEFAULT_MAX_LIST_ENTRIES = 200
DEFAULT_PYTHON_TIMEOUT_SECONDS = 10
DEFAULT_MAX_TOOL_ROUNDS = 2
DEFAULT_PYTHON_PROCESS_MEMORY_BYTES = 256 * 1024 * 1024
DEFAULT_PYTHON_JOB_MEMORY_BYTES = 512 * 1024 * 1024
DEFAULT_PYTHON_CPU_TIME_SECONDS = 10
DEFAULT_PYTHON_MAX_PROCESSES = 8
MIN_PYTHON_PROCESS_MEMORY_BYTES = 16 * 1024 * 1024
MAX_PYTHON_PROCESS_MEMORY_BYTES = 1024 * 1024 * 1024
MIN_PYTHON_JOB_MEMORY_BYTES = 32 * 1024 * 1024
MAX_PYTHON_JOB_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
MIN_PYTHON_CPU_TIME_SECONDS = 1
MAX_PYTHON_CPU_TIME_SECONDS = 60
MIN_PYTHON_MAX_PROCESSES = 1
MAX_PYTHON_MAX_PROCESSES = 64

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _resolve_path(raw: str | None, root: Path, default: Path) -> Path:
    if not raw or not raw.strip():
        return default
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return default
    return candidate


@dataclass(frozen=True)
class ToolPolicy:
    project_root: Path = PROJECT_ROOT
    read_root: Path | None = None
    write_root: Path | None = None
    python_workdir: Path | None = None
    log_path: Path | None = None
    tools_enabled: bool = False
    allow_writes: bool = False
    allow_python: bool = False
    auto_approve_reads: bool = True
    require_confirmation: bool = True
    log_content: bool = False
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_code_bytes: int = DEFAULT_MAX_CODE_BYTES
    max_list_entries: int = DEFAULT_MAX_LIST_ENTRIES
    python_timeout_seconds: int = DEFAULT_PYTHON_TIMEOUT_SECONDS
    python_process_memory_bytes: int = DEFAULT_PYTHON_PROCESS_MEMORY_BYTES
    python_job_memory_bytes: int = DEFAULT_PYTHON_JOB_MEMORY_BYTES
    python_cpu_time_seconds: int = DEFAULT_PYTHON_CPU_TIME_SECONDS
    python_max_processes: int = DEFAULT_PYTHON_MAX_PROCESSES
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS

    def __post_init__(self) -> None:
        root = Path(self.project_root).resolve()
        read_root = (
            Path(self.read_root).resolve() if self.read_root is not None else root
        )
        write_root = (
            Path(self.write_root).resolve()
            if self.write_root is not None
            else root / "workspace"
        )
        python_workdir = (
            Path(self.python_workdir).resolve()
            if self.python_workdir is not None
            else root / "workspace" / "python"
        )
        log_path = (
            Path(self.log_path).resolve()
            if self.log_path is not None
            else root / "logs" / "tool_calls.jsonl"
        )

        for label, path in (
            ("read_root", read_root),
            ("write_root", write_root),
            ("python_workdir", python_workdir),
            ("log_path", log_path),
        ):
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{label} must be inside project_root.") from exc

        object.__setattr__(self, "project_root", root)
        object.__setattr__(self, "read_root", read_root)
        object.__setattr__(self, "write_root", write_root)
        object.__setattr__(self, "python_workdir", python_workdir)
        object.__setattr__(self, "log_path", log_path)
        object.__setattr__(
            self,
            "max_file_bytes",
            max(1024, min(1024 * 1024, self.max_file_bytes)),
        )
        object.__setattr__(
            self,
            "max_output_bytes",
            max(1024, min(64 * 1024, self.max_output_bytes)),
        )
        object.__setattr__(
            self,
            "max_code_bytes",
            max(1024, min(DEFAULT_MAX_CODE_BYTES, self.max_code_bytes)),
        )
        object.__setattr__(
            self,
            "max_list_entries",
            max(1, min(1000, self.max_list_entries)),
        )
        object.__setattr__(
            self,
            "python_timeout_seconds",
            max(
                1,
                min(
                    DEFAULT_PYTHON_TIMEOUT_SECONDS,
                    self.python_timeout_seconds,
                ),
            ),
        )
        object.__setattr__(
            self,
            "python_process_memory_bytes",
            max(
                MIN_PYTHON_PROCESS_MEMORY_BYTES,
                min(
                    MAX_PYTHON_PROCESS_MEMORY_BYTES,
                    self.python_process_memory_bytes,
                ),
            ),
        )
        object.__setattr__(
            self,
            "python_job_memory_bytes",
            max(
                MIN_PYTHON_JOB_MEMORY_BYTES,
                min(
                    MAX_PYTHON_JOB_MEMORY_BYTES,
                    self.python_job_memory_bytes,
                ),
            ),
        )
        object.__setattr__(
            self,
            "python_cpu_time_seconds",
            max(
                MIN_PYTHON_CPU_TIME_SECONDS,
                min(
                    MAX_PYTHON_CPU_TIME_SECONDS,
                    self.python_cpu_time_seconds,
                ),
            ),
        )
        object.__setattr__(
            self,
            "python_max_processes",
            max(
                MIN_PYTHON_MAX_PROCESSES,
                min(
                    MAX_PYTHON_MAX_PROCESSES,
                    self.python_max_processes,
                ),
            ),
        )
        object.__setattr__(
            self,
            "max_tool_rounds",
            max(1, min(4, self.max_tool_rounds)),
        )

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "ToolPolicy":
        root = Path(project_root or PROJECT_ROOT).resolve()
        read_default = root
        write_default = root / "workspace"
        python_default = root / "workspace" / "python"
        log_default = root / "logs" / "tool_calls.jsonl"

        read_root = _resolve_path(
            os.getenv("TOOL_READ_ROOT"), root, read_default
        )
        write_root = _resolve_path(
            os.getenv("TOOL_WRITE_ROOT"), root, write_default
        )
        python_workdir = _resolve_path(
            os.getenv("TOOL_PYTHON_WORKDIR"), root, python_default
        )
        log_path = _resolve_path(
            os.getenv("TOOL_LOG_PATH"), root, log_default
        )

        return cls(
            project_root=root,
            read_root=read_root,
            write_root=write_root,
            python_workdir=python_workdir,
            log_path=log_path,
            tools_enabled=_env_bool("TOOLS_ENABLED", False),
            allow_writes=_env_bool("TOOL_ALLOW_WRITES", False),
            allow_python=_env_bool("TOOL_ALLOW_PYTHON", False),
            auto_approve_reads=_env_bool("TOOL_AUTO_APPROVE_READS", True),
            require_confirmation=_env_bool("TOOL_REQUIRE_CONFIRMATION", True),
            log_content=_env_bool("TOOL_LOG_CONTENT", False),
            max_file_bytes=_env_int(
                "TOOL_MAX_FILE_BYTES",
                DEFAULT_MAX_FILE_BYTES,
                1024,
                1024 * 1024,
            ),
            max_output_bytes=_env_int(
                "TOOL_MAX_OUTPUT_BYTES",
                DEFAULT_MAX_OUTPUT_BYTES,
                1024,
                64 * 1024,
            ),
            max_code_bytes=_env_int(
                "TOOL_MAX_CODE_BYTES",
                DEFAULT_MAX_CODE_BYTES,
                1024,
                DEFAULT_MAX_CODE_BYTES,
            ),
            max_list_entries=_env_int(
                "TOOL_MAX_LIST_ENTRIES",
                DEFAULT_MAX_LIST_ENTRIES,
                1,
                1000,
            ),
            python_timeout_seconds=_env_int(
                "TOOL_PYTHON_TIMEOUT_SECONDS",
                DEFAULT_PYTHON_TIMEOUT_SECONDS,
                1,
                DEFAULT_PYTHON_TIMEOUT_SECONDS,
            ),
            python_process_memory_bytes=_env_int(
                "TOOL_PYTHON_PROCESS_MEMORY_BYTES",
                DEFAULT_PYTHON_PROCESS_MEMORY_BYTES,
                MIN_PYTHON_PROCESS_MEMORY_BYTES,
                MAX_PYTHON_PROCESS_MEMORY_BYTES,
            ),
            python_job_memory_bytes=_env_int(
                "TOOL_PYTHON_JOB_MEMORY_BYTES",
                DEFAULT_PYTHON_JOB_MEMORY_BYTES,
                MIN_PYTHON_JOB_MEMORY_BYTES,
                MAX_PYTHON_JOB_MEMORY_BYTES,
            ),
            python_cpu_time_seconds=_env_int(
                "TOOL_PYTHON_CPU_TIME_SECONDS",
                DEFAULT_PYTHON_CPU_TIME_SECONDS,
                MIN_PYTHON_CPU_TIME_SECONDS,
                MAX_PYTHON_CPU_TIME_SECONDS,
            ),
            python_max_processes=_env_int(
                "TOOL_PYTHON_MAX_PROCESSES",
                DEFAULT_PYTHON_MAX_PROCESSES,
                MIN_PYTHON_MAX_PROCESSES,
                MAX_PYTHON_MAX_PROCESSES,
            ),
            max_tool_rounds=_env_int(
                "TOOL_MAX_ROUNDS",
                DEFAULT_MAX_TOOL_ROUNDS,
                1,
                4,
            ),
        )

    def denial_reason(self, spec: ToolSpec) -> str | None:
        if not self.tools_enabled:
            return "Tools are disabled."
        if spec.requires_python and not self.allow_python:
            return "Python execution is disabled."
        if spec.mutating and not spec.requires_python and not self.allow_writes:
            return "Write tools are disabled."
        return None

    def approval_required(self, spec: ToolSpec) -> bool:
        if spec.mutating:
            return True
        if spec.requires_python:
            return self.require_confirmation
        if spec.requires_approval:
            return True
        return not self.auto_approve_reads


__all__ = [
    "PROJECT_ROOT",
    "ToolPolicy",
]
