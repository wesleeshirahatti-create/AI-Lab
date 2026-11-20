import unicodedata

from assistant import Assistant
from assistant.tools import (
    ToolPolicy,
    UnsafePathError,
    resolve_write_path,
)
from config import Settings


def _ask(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes"}


def _escape_code_line(line: str) -> str:
    escaped: list[str] = []
    for character in line:
        category = unicodedata.category(character)
        if character == "\t":
            escaped.append("\\t")
        elif category.startswith("C") or category in {"Zl", "Zp"}:
            code = ord(character)
            if code <= 0xFF:
                escaped.append(f"\\x{code:02x}")
            elif code <= 0xFFFF:
                escaped.append(f"\\u{code:04x}")
            else:
                escaped.append(f"\\U{code:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _print_python_code(code: str) -> None:
    lines = code.splitlines()
    width = len(str(max(1, len(lines))))
    for number, line in enumerate(lines, 1):
        print(f"  {number:>{width}} | {_escape_code_line(line)}")


def _build_approver(policy: ToolPolicy):
    def approve(spec, arguments):
        if spec.name == "write_file":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            size = (
                len(content.encode("utf-8"))
                if isinstance(content, str)
                else 0
            )
            overwrite = arguments.get("overwrite", False)
            print("\nApproval required: write_file")
            print(f"  Path: {path}")
            print(f"  Content: {size} bytes")
            print(f"  Overwrite: {'yes' if overwrite else 'no'}")
            if not _ask("Approve this write? [y/N] "):
                return False
            if overwrite:
                try:
                    target = resolve_write_path(
                        policy.write_root, path
                    )
                except UnsafePathError:
                    return False
                if target.exists():
                    print(f"WARNING: '{path}' already exists.")
                    if not _ask(
                        "Approve overwriting this file? [y/N] "
                    ):
                        return False
            return True

        if spec.name == "run_python":
            code = arguments.get("code", "")
            if not isinstance(code, str):
                code = ""
            size = len(code.encode("utf-8"))
            timeout = arguments.get(
                "timeout_seconds", policy.python_timeout_seconds
            )
            if isinstance(timeout, bool) or not isinstance(timeout, int):
                timeout = policy.python_timeout_seconds
            timeout = max(1, min(policy.python_timeout_seconds, timeout))

            print("\nApproval required: run_python")
            print(f"  Code: {size} bytes")
            print(f"  Timeout: {timeout} seconds")
            print(f"  Working directory: {policy.python_workdir}")
            print("  Each run uses a new temporary subdirectory.")
            print(
                "  WARNING: Python runs with your user permissions and is "
                "not a security sandbox."
            )
            print("  Code:")
            _print_python_code(code)
            return _ask("Approve this Python execution? [y/N] ")

        print(f"\nApproval required: {spec.name}")
        return _ask("Approve this tool call? [y/N] ")

    return approve


def main() -> None:
    settings = Settings.from_env()
    policy = ToolPolicy.from_env()
    assistant = Assistant(
        settings,
        policy=policy,
        approver=_build_approver(policy),
    )

    print(f"{settings.assistant_name} (type 'help' for help, 'exit' to quit)")

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if message.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        if not message:
            continue

        try:
            response = assistant.reply(message)
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break

        print(f"{settings.assistant_name}: {response}")


if __name__ == "__main__":
    main()
