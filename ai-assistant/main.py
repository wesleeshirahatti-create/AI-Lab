from assistant import Assistant
from config import Settings


def main() -> None:
    settings = Settings.from_env()
    assistant = Assistant(settings)

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
