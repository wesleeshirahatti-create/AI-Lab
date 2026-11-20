import tempfile
import unittest
from pathlib import Path

from assistant.tools import ToolRegistry, build_default_registry

from support import FakeTool


class ToolRegistryTests(unittest.TestCase):
    def test_default_registry_has_no_tools(self):
        registry = build_default_registry()
        self.assertEqual(registry.names(), ())
        self.assertEqual(len(registry), 0)
        self.assertIsNone(registry.get("read_file"))

    def test_register_lookup_and_specs(self):
        registry = ToolRegistry()
        tool = FakeTool("read_file")
        registry.register(tool)

        self.assertIs(registry.get("read_file"), tool)
        self.assertIn("read_file", registry)
        self.assertEqual(registry.names(), ("read_file",))
        self.assertEqual(registry.specs(), (tool.spec,))
        self.assertEqual(len(registry), 1)

    def test_initial_mapping_is_registered_by_spec_name(self):
        tool = FakeTool("read_file")
        registry = ToolRegistry({"ignored_key": tool})
        self.assertIs(registry.get("read_file"), tool)
        self.assertIsNone(registry.get("ignored_key"))

    def test_duplicate_registration_is_rejected(self):
        registry = ToolRegistry()
        registry.register(FakeTool("read_file"))
        with self.assertRaises(ValueError):
            registry.register(FakeTool("read_file"))

    def test_invalid_tool_is_rejected(self):
        registry = ToolRegistry()
        with self.assertRaises(TypeError):
            registry.register(object())

    def test_registry_does_not_discover_tools_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "not_a_tool.py").write_text(
                "raise RuntimeError('must not be imported')\n",
                encoding="utf-8",
            )
            registry = build_default_registry()
        self.assertEqual(registry.names(), ())


if __name__ == "__main__":
    unittest.main()
