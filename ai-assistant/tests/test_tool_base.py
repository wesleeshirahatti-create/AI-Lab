import unittest

from assistant.tools.base import (
    ToolError,
    ToolResult,
    ToolSpec,
    ToolValidationError,
)


class ToolSpecTests(unittest.TestCase):
    def test_accepts_valid_spec(self):
        spec = ToolSpec(
            name="read_file",
            description="Read a file.",
            parameters={"type": "object"},
        )
        self.assertEqual(spec.name, "read_file")
        self.assertFalse(spec.mutating)
        self.assertFalse(spec.requires_approval)
        self.assertFalse(spec.requires_python)

    def test_rejects_invalid_names(self):
        for name in ("", "ReadFile", "read-file", "1read", "_read", "read file"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    ToolSpec(name=name, description="A tool.")

    def test_rejects_empty_description(self):
        with self.assertRaises(ValueError):
            ToolSpec(name="read_file", description="  ")

    def test_rejects_non_mapping_parameters(self):
        with self.assertRaises(ValueError):
            ToolSpec(
                name="read_file",
                description="Read a file.",
                parameters=["not", "a", "mapping"],
            )

    def test_rejects_non_boolean_flags(self):
        for attribute in ("mutating", "requires_approval", "requires_python"):
            with self.subTest(attribute=attribute):
                with self.assertRaises(ValueError):
                    ToolSpec(
                        name="read_file",
                        description="Read a file.",
                        **{attribute: 1},
                    )

    def test_parameters_are_copied(self):
        parameters = {"type": "object"}
        spec = ToolSpec(
            name="read_file",
            description="Read a file.",
            parameters=parameters,
        )
        parameters["type"] = "string"
        self.assertEqual(spec.parameters["type"], "object")


class ToolResultTests(unittest.TestCase):
    def test_success_round_trip(self):
        result = ToolResult.success(
            "read_file", "contents", {"bytes": 8}
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "contents")
        self.assertEqual(
            result.to_dict(),
            {
                "ok": True,
                "tool": "read_file",
                "output": "contents",
                "error": "",
                "metadata": {"bytes": 8},
            },
        )

    def test_failure_round_trip(self):
        result = ToolResult.failure("read_file", "denied")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "denied")
        self.assertEqual(result.to_dict()["error"], "denied")

    def test_metadata_is_copied(self):
        metadata = {"bytes": 1}
        result = ToolResult.success("read_file", metadata=metadata)
        metadata["bytes"] = 2
        self.assertEqual(result.metadata["bytes"], 1)

    def test_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            ToolResult(ok="yes", tool="read_file")
        with self.assertRaises(ValueError):
            ToolResult(ok=True, tool="")
        with self.assertRaises(ValueError):
            ToolResult(ok=True, tool="read_file", output=None)
        with self.assertRaises(ValueError):
            ToolResult(ok=True, tool="read_file", error=None)
        with self.assertRaises(ValueError):
            ToolResult(ok=True, tool="read_file", metadata=[])


class ToolExceptionTests(unittest.TestCase):
    def test_validation_error_is_tool_error(self):
        self.assertTrue(issubclass(ToolValidationError, ToolError))


if __name__ == "__main__":
    unittest.main()
