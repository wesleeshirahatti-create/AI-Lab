from collections.abc import Mapping

from .base import Tool, ToolSpec


class ToolRegistry:
    def __init__(self, tools: Mapping[str, Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools.values():
                self.register(tool)

    def register(self, tool: Tool) -> None:
        spec = getattr(tool, "spec", None)
        if not isinstance(spec, ToolSpec):
            raise TypeError("Tool must expose a ToolSpec as 'spec'.")
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        self._tools[spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools[name].spec for name in self.names())

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]
