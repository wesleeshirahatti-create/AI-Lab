import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base import ToolResult, ToolSpec, ToolValidationError
from .policy import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_LIST_ENTRIES,
    DEFAULT_MAX_OUTPUT_BYTES,
    PROJECT_ROOT,
    ToolPolicy,
)

DEFAULT_READ_MAX_CHARS = 8000
DEFAULT_MAX_CHARS = DEFAULT_READ_MAX_CHARS
DEFAULT_MAX_WRITE_BYTES = DEFAULT_MAX_FILE_BYTES
MAX_WRITE_BYTES = DEFAULT_MAX_FILE_BYTES
MAX_WRITE_PATH_LENGTH = 4096

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')
_PATH_SEPARATOR_PATTERN = re.compile(r"[\\/]+")


class UnsafePathError(ToolValidationError):
    pass


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _has_windows_reserved_name(path: Path) -> bool:
    for part in path.parts:
        if not part or part in {".", ".."}:
            continue
        name = part.split(":", 1)[0].rstrip(" .").upper()
        stem = name.split(".", 1)[0].rstrip(" .")
        if name in _WINDOWS_RESERVED_NAMES or stem in _WINDOWS_RESERVED_NAMES:
            return True
    return False


def resolve_workspace_path(root: Path, raw_path: str) -> Path:
    root = Path(root).resolve()
    if not isinstance(raw_path, str):
        raise UnsafePathError("Path must be a string.")
    if "\x00" in raw_path:
        raise UnsafePathError("Path contains an invalid character.")

    value = raw_path.strip()
    if not value:
        raise UnsafePathError("Path must not be empty.")
    if value.startswith(("~", "/", "\\")) or _WINDOWS_DRIVE_PATTERN.match(value):
        raise UnsafePathError("Absolute or home paths are not allowed.")

    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive:
        raise UnsafePathError("Absolute paths are not allowed.")
    if any(
        part == ".."
        for part in _PATH_SEPARATOR_PATTERN.split(value)
    ) or any(part == ".." for part in candidate.parts):
        raise UnsafePathError("Path traversal is not allowed.")
    if os.name == "nt" and _has_windows_reserved_name(candidate):
        raise UnsafePathError("Reserved device paths are not allowed.")

    try:
        resolved = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise UnsafePathError("Path could not be resolved.") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError("Path is outside the workspace.") from exc

    return resolved


def _validate_write_path_components(value: str) -> None:
    if value.endswith(("/", "\\")):
        raise UnsafePathError("Path must name a file, not a directory.")
    if len(value) > MAX_WRITE_PATH_LENGTH:
        raise UnsafePathError("Path is too long.")

    for part in _PATH_SEPARATOR_PATTERN.split(value):
        if part == "..":
            raise UnsafePathError("Path traversal is not allowed.")
        if part == "":
            raise UnsafePathError("Path contains an empty component.")
        if part == ".":
            continue
        if part != part.strip():
            raise UnsafePathError(
                "Path components must not start or end with whitespace."
            )
        if ":" in part:
            raise UnsafePathError(
                "Alternate data streams are not allowed."
            )
        if part.endswith((".", " ")):
            raise UnsafePathError(
                "Path components must not end with a dot or space."
            )
        if any(
            character in _WINDOWS_FORBIDDEN_CHARACTERS
            or ord(character) < 32
            for character in part
        ):
            raise UnsafePathError("Path contains invalid characters.")

    if _has_windows_reserved_name(Path(value)):
        raise UnsafePathError("Reserved device paths are not allowed.")


def resolve_write_path(root: Path, raw_path: str) -> Path:
    root = Path(root).resolve()
    if not isinstance(raw_path, str):
        raise UnsafePathError("Path must be a string.")
    if "\x00" in raw_path:
        raise UnsafePathError("Path contains an invalid character.")

    value = raw_path
    if not value.strip():
        raise UnsafePathError("Path must not be empty.")
    if value != value.strip():
        raise UnsafePathError(
            "Path must not start or end with whitespace."
        )
    if value.startswith(("~", "/", "\\")) or _WINDOWS_DRIVE_PATTERN.match(
        value
    ):
        raise UnsafePathError("Absolute or home paths are not allowed.")
    if ":" in value:
        raise UnsafePathError("Alternate data streams are not allowed.")

    candidate = Path(value)
    if candidate.is_absolute() or candidate.drive:
        raise UnsafePathError("Absolute paths are not allowed.")
    if any(
        part == ".."
        for part in _PATH_SEPARATOR_PATTERN.split(value)
    ) or any(part == ".." for part in candidate.parts):
        raise UnsafePathError("Path traversal is not allowed.")

    _validate_write_path_components(value)

    try:
        resolved = (root / candidate).resolve()
    except (OSError, RuntimeError) as exc:
        raise UnsafePathError("Path could not be resolved.") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError("Path is outside the workspace.") from exc
    if resolved == root:
        raise UnsafePathError("Path must name a file.")

    return resolved


def _atomic_write(target: Path, data: bytes, *, overwrite: bool) -> None:
    parent = target.parent
    temp_path: Path | None = None
    descriptor = -1

    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".write-",
            suffix=".tmp",
            dir=str(parent),
        )
        temp_path = Path(temp_name)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())

        if overwrite:
            os.replace(temp_path, target)
            temp_path = None
        else:
            os.link(temp_path, target)
    finally:
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return True
    sample = data[:4096]
    control_bytes = sum(
        1
        for byte in sample
        if byte < 32 and byte not in {9, 10, 13}
    )
    return control_bytes / len(sample) > 0.30


class ListFilesTool:
    spec = ToolSpec(
        name="list_files",
        description=(
            "List the immediate files and directories in a workspace path. "
            "Read-only; paths must stay inside the assistant workspace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Directory path relative to the assistant workspace. "
                        "Defaults to '.'."
                    ),
                }
            },
            "additionalProperties": False,
        },
    )

    def __init__(
        self,
        root: Path | str | ToolPolicy | None = None,
        *,
        policy: ToolPolicy | None = None,
        max_list_entries: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        if isinstance(root, ToolPolicy):
            policy = root
            root = None
        if policy is not None:
            root = root or policy.read_root
            if max_list_entries is None:
                max_list_entries = policy.max_list_entries
            if max_output_bytes is None:
                max_output_bytes = policy.max_output_bytes

        self.root = Path(root or PROJECT_ROOT).resolve()
        self.max_list_entries = _clamp(
            int(
                max_list_entries
                if max_list_entries is not None
                else DEFAULT_MAX_LIST_ENTRIES
            ),
            1,
            1000,
        )
        self.max_output_bytes = _clamp(
            int(
                max_output_bytes
                if max_output_bytes is not None
                else DEFAULT_MAX_OUTPUT_BYTES
            ),
            1024,
            64 * 1024,
        )

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("Tool arguments must be an object.")

        unknown = set(arguments) - {"path"}
        if unknown:
            raise ToolValidationError(
                "Unknown argument(s): " + ", ".join(sorted(map(str, unknown)))
            )

        raw_path = arguments.get("path", ".")
        if raw_path is None:
            raw_path = "."
        if not isinstance(raw_path, str):
            raise ToolValidationError("'path' must be a string.")

        normalized = raw_path.strip() or "."
        resolve_workspace_path(self.root, normalized)
        return {"path": normalized}

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            validated = self.validate(arguments)
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            target = resolve_workspace_path(self.root, validated["path"])
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            if not target.exists():
                return ToolResult.failure(self.spec.name, "Directory not found.")
            if not target.is_dir():
                return ToolResult.failure(self.spec.name, "Path is not a directory.")
            with os.scandir(target) as scanner:
                names = [entry.name for entry in scanner]
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return ToolResult.failure(
                self.spec.name, f"Could not list directory: {detail}"
            )

        names.sort(key=lambda name: (name.lower(), name))
        total_count = len(names)
        truncated = total_count > self.max_list_entries
        names = names[: self.max_list_entries]
        output = "\n".join(names)

        if len(output.encode("utf-8")) > self.max_output_bytes:
            output = _truncate_utf8(output, self.max_output_bytes)
            truncated = True

        return ToolResult.success(
            self.spec.name,
            output,
            {
                "path": _display_path(self.root, target),
                "count": len(names),
                "total_count": total_count,
                "truncated": truncated,
            },
        )


class ReadFileTool:
    spec = ToolSpec(
        name="read_file",
        description=(
            "Read a UTF-8 text file from the assistant workspace. "
            "Read-only; binary, oversized, and out-of-workspace files are rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative to the assistant workspace."
                    ),
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Maximum number of characters to return "
                        f"(default: {DEFAULT_READ_MAX_CHARS})."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def __init__(
        self,
        root: Path | str | ToolPolicy | None = None,
        *,
        policy: ToolPolicy | None = None,
        max_file_bytes: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        if isinstance(root, ToolPolicy):
            policy = root
            root = None
        if policy is not None:
            root = root or policy.read_root
            if max_file_bytes is None:
                max_file_bytes = policy.max_file_bytes
            if max_output_bytes is None:
                max_output_bytes = policy.max_output_bytes

        self.root = Path(root or PROJECT_ROOT).resolve()
        self.max_file_bytes = _clamp(
            int(
                max_file_bytes
                if max_file_bytes is not None
                else DEFAULT_MAX_FILE_BYTES
            ),
            1024,
            1024 * 1024,
        )
        self.max_output_bytes = _clamp(
            int(
                max_output_bytes
                if max_output_bytes is not None
                else DEFAULT_MAX_OUTPUT_BYTES
            ),
            1024,
            64 * 1024,
        )

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("Tool arguments must be an object.")

        unknown = set(arguments) - {"path", "max_chars"}
        if unknown:
            raise ToolValidationError(
                "Unknown argument(s): " + ", ".join(sorted(map(str, unknown)))
            )

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolValidationError("'path' must be a non-empty string.")

        max_chars = arguments.get("max_chars", DEFAULT_READ_MAX_CHARS)
        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise ToolValidationError("'max_chars' must be an integer.")
        if max_chars < 1:
            raise ToolValidationError("'max_chars' must be at least 1.")

        normalized = raw_path.strip()
        resolve_workspace_path(self.root, normalized)
        return {
            "path": normalized,
            "max_chars": min(max_chars, self.max_output_bytes),
        }

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            validated = self.validate(arguments)
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            target = resolve_workspace_path(self.root, validated["path"])
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            if not target.exists():
                return ToolResult.failure(self.spec.name, "File not found.")
            if not target.is_file():
                return ToolResult.failure(self.spec.name, "Path is not a file.")
            size = target.stat().st_size
            if size > self.max_file_bytes:
                return ToolResult.failure(
                    self.spec.name,
                    f"File is too large ({size} bytes; "
                    f"limit is {self.max_file_bytes}).",
                )
            with target.open("rb") as handle:
                data = handle.read(self.max_file_bytes + 1)
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return ToolResult.failure(
                self.spec.name, f"Could not read file: {detail}"
            )

        if len(data) > self.max_file_bytes:
            return ToolResult.failure(
                self.spec.name,
                f"File is too large (limit is {self.max_file_bytes} bytes).",
            )
        if _looks_binary(data):
            return ToolResult.failure(
                self.spec.name, "Binary files are not supported."
            )

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(
                self.spec.name, "Binary files are not supported."
            )

        total_chars = len(text)
        output = text[: validated["max_chars"]]
        truncated = len(output) < total_chars
        output = _truncate_utf8(output, self.max_output_bytes)
        if len(output) < min(total_chars, validated["max_chars"]):
            truncated = True

        return ToolResult.success(
            self.spec.name,
            output,
            {
                "path": _display_path(self.root, target),
                "bytes": len(data),
                "chars": len(output),
                "total_chars": total_chars,
                "truncated": truncated,
            },
        )


class WriteFileTool:
    spec = ToolSpec(
        name="write_file",
        description=(
            "Write a UTF-8 text file inside the assistant workspace. "
            "Each write requires explicit user approval. Existing files are "
            "only replaced when overwrite=true and the overwrite is approved."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File path relative to the assistant workspace."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text to write.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Replace an existing file. Requires explicit "
                        "overwrite approval."
                    ),
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        mutating=True,
        requires_approval=True,
    )

    def __init__(
        self,
        root: Path | str | ToolPolicy | None = None,
        *,
        policy: ToolPolicy | None = None,
        max_file_bytes: int | None = None,
    ) -> None:
        if isinstance(root, ToolPolicy):
            policy = root
            root = None
        if policy is not None:
            root = root or policy.write_root
            if max_file_bytes is None:
                max_file_bytes = policy.max_file_bytes

        self.root = Path(root or (PROJECT_ROOT / "workspace")).resolve()
        limit = (
            max_file_bytes
            if max_file_bytes is not None
            else DEFAULT_MAX_WRITE_BYTES
        )
        self.max_file_bytes = max(1, min(MAX_WRITE_BYTES, int(limit)))

    def validate(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise ToolValidationError("Tool arguments must be an object.")

        unknown = set(arguments) - {"path", "content", "overwrite"}
        if unknown:
            raise ToolValidationError(
                "Unknown argument(s): " + ", ".join(sorted(map(str, unknown)))
            )

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolValidationError("'path' must be a non-empty string.")
        if raw_path != raw_path.strip():
            raise ToolValidationError(
                "'path' must not start or end with whitespace."
            )

        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolValidationError("'content' must be a string.")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolValidationError(
                "'content' must be valid UTF-8 text."
            ) from exc
        if len(encoded) > self.max_file_bytes:
            raise ToolValidationError(
                f"Content is too large ({len(encoded)} bytes; "
                f"limit is {self.max_file_bytes})."
            )

        overwrite = arguments.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ToolValidationError("'overwrite' must be a boolean.")

        resolve_write_path(self.root, raw_path)
        return {
            "path": raw_path,
            "content": content,
            "overwrite": overwrite,
        }

    def execute(self, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            validated = self.validate(arguments)
        except ToolValidationError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            data = validated["content"].encode("utf-8")
        except UnicodeEncodeError:
            return ToolResult.failure(
                self.spec.name, "'content' must be valid UTF-8 text."
            )
        if len(data) > self.max_file_bytes:
            return ToolResult.failure(
                self.spec.name,
                f"Content is too large ({len(data)} bytes; "
                f"limit is {self.max_file_bytes}).",
            )

        try:
            target = resolve_write_path(self.root, validated["path"])
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target = resolve_write_path(self.root, validated["path"])
        except UnsafePathError as exc:
            return ToolResult.failure(self.spec.name, str(exc))
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return ToolResult.failure(
                self.spec.name, f"Could not create directory: {detail}"
            )

        if target.exists():
            if target.is_dir():
                return ToolResult.failure(
                    self.spec.name, "Path is a directory, not a file."
                )
            if not target.is_file():
                return ToolResult.failure(
                    self.spec.name, "Path is not a regular file."
                )
            if not validated["overwrite"]:
                return ToolResult.failure(
                    self.spec.name,
                    "File already exists; set overwrite=true to replace it.",
                )
            existed = True
        else:
            existed = False

        try:
            _atomic_write(
                target, data, overwrite=validated["overwrite"]
            )
        except FileExistsError:
            return ToolResult.failure(
                self.spec.name,
                "File already exists; set overwrite=true to replace it.",
            )
        except OSError as exc:
            detail = exc.strerror or str(exc)
            return ToolResult.failure(
                self.spec.name, f"Could not write file: {detail}"
            )

        display = _display_path(self.root, target)
        return ToolResult.success(
            self.spec.name,
            f"Wrote {display} ({len(data)} bytes).",
            {
                "path": display,
                "bytes": len(data),
                "created": not existed,
                "overwritten": existed and validated["overwrite"],
            },
        )


ListFiles = ListFilesTool
ReadFile = ReadFileTool
WriteFile = WriteFileTool


__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_WRITE_BYTES",
    "DEFAULT_READ_MAX_CHARS",
    "ListFiles",
    "ListFilesTool",
    "MAX_WRITE_BYTES",
    "ReadFile",
    "ReadFileTool",
    "UnsafePathError",
    "WriteFile",
    "WriteFileTool",
    "resolve_workspace_path",
    "resolve_write_path",
]
