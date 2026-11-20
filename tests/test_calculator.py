import pytest

import calculator


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (2, 3, 5),
        (-2, 3, 1),
        (0, 0, 0),
        (1.5, 2.5, 4.0),
        (1_000_000, 2_000_000, 3_000_000),
    ],
)
def test_add(a, b, expected):
    assert calculator.add(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5, 3, 2),
        (3, 5, -2),
        (-4, -6, 2),
        (2.5, 0.5, 2.0),
        (0, 7, -7),
    ],
)
def test_subtract(a, b, expected):
    assert calculator.subtract(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (4, 3, 12),
        (-2, 5, -10),
        (0, 9, 0),
        (1.5, 2, 3.0),
        (-3, -4, 12),
    ],
)
def test_multiply(a, b, expected):
    assert calculator.multiply(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (10, 2, 5.0),
        (7, 2, 3.5),
        (-9, 3, -3.0),
        (1, 4, 0.25),
        (0, 5, 0.0),
    ],
)
def test_divide(a, b, expected):
    assert calculator.divide(a, b) == expected


@pytest.mark.parametrize("denominator", [0, 0.0])
def test_divide_by_zero_raises_error(denominator):
    with pytest.raises(ZeroDivisionError, match="Cannot divide by zero"):
        calculator.divide(10, denominator)


def test_get_number_reprompts_until_valid(monkeypatch, capsys):
    responses = iter(["", "not-a-number", "  7.5  "])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    result = calculator.get_number("Enter a number: ")

    assert result == 7.5
    output = capsys.readouterr().out
    assert output.count("Invalid input. Please enter a number.") == 2


def test_get_operation_rejects_invalid_choice(monkeypatch, capsys):
    responses = iter(["9", "3"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    operation = calculator.get_operation()

    assert operation == ("Multiplication", calculator.multiply)
    assert "Invalid choice. Please select 1, 2, 3, 4, or 5." in capsys.readouterr().out


def test_get_operation_quit_returns_none(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "5")

    assert calculator.get_operation() is None


def test_should_continue_rejects_invalid_input(monkeypatch, capsys):
    responses = iter(["maybe", "YES"])
    monkeypatch.setattr("builtins.input", lambda _: next(responses))

    assert calculator.should_continue() is True
    assert "Invalid input. Please enter 'y' or 'n'." in capsys.readouterr().out


def test_should_continue_accepts_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "no")

    assert calculator.should_continue() is False
