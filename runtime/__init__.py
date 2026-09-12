"""NexusSearch runtime: graph, evidence, MCP routing, loop, gate, report.

The skill is agent-first: a host agent (Claude Code / Codex / Qoder / Copilot)
reads ``SKILL.md`` and either drives the subcommands below step by step, or
calls ``nexus run`` and lets the loop drive the local MCP servers itself.

Modules are intentionally independent of any vendor SDK - only the Python
standard library plus PyYAML/jsonschema (both optional: see ``runtime.util``).
"""

from __future__ import annotations

from .agent_router import AgentRouter
from .evidence_store import EvidenceStore
from .graph_engine import GraphError, ResearchGraph
from .loop_controller import LoopController, LoopIteration
from .mcp_router import McpError, McpResult, McpRouter
from .memory_store import MemoryStore, Pattern
from .models import AgentMessage, Evidence, GraphEdge, GraphNode, GraphUpdate, ResearchTask
from .quality_gate import CheckResult, GateReport, QualityGate
from .report_builder import ReportBuilder
from .workspace import Workspace

__version__ = "1.2.1"

__all__ = [
    "AgentMessage",
    "AgentRouter",
    "CheckResult",
    "Evidence",
    "EvidenceStore",
    "GateReport",
    "GraphEdge",
    "GraphError",
    "GraphNode",
    "GraphUpdate",
    "LoopController",
    "LoopIteration",
    "McpError",
    "McpResult",
    "McpRouter",
    "MemoryStore",
    "Pattern",
    "QualityGate",
    "ReportBuilder",
    "ResearchGraph",
    "ResearchTask",
    "Workspace",
    "__version__",
]
