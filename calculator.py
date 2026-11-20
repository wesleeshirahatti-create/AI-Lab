def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero.")
    return a / b


OPERATIONS = {
    "1": ("Addition", add),
    "2": ("Subtraction", subtract),
    "3": ("Multiplication", multiply),
    "4": ("Division", divide),
}


def get_number(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return float(raw)
        except ValueError:
            print("Invalid input. Please enter a number.")


def get_operation():
    while True:
        print("\nCalculator Menu")
        print("1. Addition")
        print("2. Subtraction")
        print("3. Multiplication")
        print("4. Division")
        print("5. Quit")
        choice = input("Choose an operation (1-5): ").strip()

        if choice == "5":
            return None
        if choice in OPERATIONS:
            return OPERATIONS[choice]
        print("Invalid choice. Please select 1, 2, 3, 4, or 5.")


def format_result(value):
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def should_continue():
    while True:
        answer = input("Would you like to perform another calculation? (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Invalid input. Please enter 'y' or 'n'.")


def main():
    print("Simple Python Calculator")

    while True:
        operation = get_operation()
        if operation is None:
            break

        name, func = operation
        print(f"\n{name} selected.")
        a = get_number("Enter the first number: ")
        b = get_number("Enter the second number: ")

        try:
            result = func(a, b)
        except ZeroDivisionError as error:
            print(f"Error: {error}")
        else:
            print(f"Result: {format_result(result)}")

        if not should_continue():
            break

    print("Goodbye!")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
