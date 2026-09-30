"""Tool foundation: the contract every SAUTI tool implements.

A tool is never allowed to run arbitrary model-authored code. The flow is
always:

    model -> tool request -> registry lookup -> schema validation
          -> permission check -> tool execution -> structured result
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class PermissionLevel(str, Enum):
    """Permission tiers.

    The agent may only auto-execute SAFE tools. Anything higher requires an
    explicit human approval step, which is not implemented yet — those tools
    are registered but disabled unless ENABLE_DANGEROUS_TOOLS is set.
    """

    SAFE = "SAFE"              # read-only, no side effects (web_search, web_reader)
    COMPUTE = "COMPUTE"        # pure computation, no I/O (calculator)
    LOCAL_READ = "LOCAL_READ"  # reads local files
    LOCAL_WRITE = "LOCAL_WRITE"
    NETWORK_WRITE = "NETWORK_WRITE"
    PROCESS = "PROCESS"        # terminal / arbitrary command execution
    SYSTEM_CONTROL = "SYSTEM_CONTROL"  # application / computer control


#: Permission levels the agent may execute without human approval.
AUTO_EXECUTABLE = {PermissionLevel.SAFE, PermissionLevel.COMPUTE}


class ToolError(Exception):
    """A tool failed in a way the agent may safely report to the user."""

    def __init__(self, message: str, *, tool_name: str = "", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.tool_name = tool_name
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "error": self.message,
            "tool": self.tool_name,
            "retryable": self.retryable,
        }


@dataclass
class ToolResult:
    """The structured outcome of a tool execution."""

    tool: str
    ok: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    sources: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = {
            "tool": self.tool,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
        }
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
        if self.sources:
            payload["sources"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.sources]
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


class Tool(abc.ABC):
    """Base class for every SAUTI tool.

    Subclasses declare their identity and contract, and implement `run`.
    """

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    output_schema: dict = {}
    permission: PermissionLevel = PermissionLevel.SAFE
    #: Tools that reach the network need timeouts and retry policy.
    requires_network: bool = False

    @abc.abstractmethod
    async def run(self, arguments: dict) -> ToolResult:
        """Execute the tool. Must not raise; return a ToolResult instead."""

    def is_enabled(self, settings: Any) -> bool:
        """Whether this tool may run under the given settings."""
        if self.permission in AUTO_EXECUTABLE:
            return True
        return bool(getattr(settings, "enable_dangerous_tools", False))
