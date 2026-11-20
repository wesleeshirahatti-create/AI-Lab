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
