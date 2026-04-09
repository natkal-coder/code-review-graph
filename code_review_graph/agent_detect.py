"""Detect calling AI agent and infer context window size.

Supports: Claude Code, Cursor, Gemini CLI, Windsurf, Zed, Continue, generic fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AgentInfo:
    """Information about calling AI agent."""

    name: str
    context_window: int  # Max tokens in agent's context window
    estimated_overhead: int  # Tokens reserved for system prompts, etc.

    def effective_capacity(self) -> int:
        """Usable tokens for application context after overhead."""
        return self.context_window - self.estimated_overhead


# Agent profiles with known context windows
AGENT_PROFILES: dict[str, AgentInfo] = {
    "claude-code": AgentInfo(
        name="Claude Code",
        context_window=200000,  # ~200k tokens
        estimated_overhead=20000,  # Reserve 20k for system
    ),
    "cursor": AgentInfo(
        name="Cursor",
        context_window=128000,  # ~128k tokens
        estimated_overhead=15000,
    ),
    "gemini-cli": AgentInfo(
        name="Gemini CLI",
        context_window=1000000,  # ~1M tokens (2.0 Flash)
        estimated_overhead=50000,
    ),
    "windsurf": AgentInfo(
        name="Windsurf",
        context_window=200000,
        estimated_overhead=20000,
    ),
    "zed": AgentInfo(
        name="Zed",
        context_window=100000,
        estimated_overhead=10000,
    ),
    "continue": AgentInfo(
        name="Continue",
        context_window=128000,
        estimated_overhead=15000,
    ),
    "generic": AgentInfo(
        name="Generic/Unknown",
        context_window=100000,
        estimated_overhead=10000,
    ),
}


def detect_agent() -> AgentInfo:
    """Detect calling agent from environment variables.

    Checks in order:
    1. CRG_AGENT_TYPE explicit env var
    2. Agent-specific env vars (set by IDE/CLI)
    3. Default to generic

    Returns:
        AgentInfo with name and context_window
    """
    # Explicit override
    explicit = os.getenv("CRG_AGENT_TYPE")
    if explicit and explicit in AGENT_PROFILES:
        return AGENT_PROFILES[explicit]

    # Auto-detect from environment
    # Claude Code sets CLAUDE_CODE environment variable
    if os.getenv("CLAUDE_CODE"):
        return AGENT_PROFILES["claude-code"]

    # Cursor sets CURSOR environment variable or CURSOR_SESSION
    if os.getenv("CURSOR") or os.getenv("CURSOR_SESSION"):
        return AGENT_PROFILES["cursor"]

    # Gemini CLI sets GEMINI_CLI
    if os.getenv("GEMINI_CLI"):
        return AGENT_PROFILES["gemini-cli"]

    # Windsurf sets WINDSURF_WORKSPACE
    if os.getenv("WINDSURF_WORKSPACE"):
        return AGENT_PROFILES["windsurf"]

    # Zed sets ZED_WORKSPACE
    if os.getenv("ZED_WORKSPACE"):
        return AGENT_PROFILES["zed"]

    # Continue sets CONTINUE_SESSION or similar
    if os.getenv("CONTINUE") or os.getenv("CONTINUE_SESSION"):
        return AGENT_PROFILES["continue"]

    # Fallback
    return AGENT_PROFILES["generic"]


def get_agent_by_name(name: str) -> Optional[AgentInfo]:
    """Get AgentInfo by name (case-insensitive)."""
    name_lower = name.lower()
    for key, agent in AGENT_PROFILES.items():
        if key == name_lower or agent.name.lower() == name_lower:
            return agent
    return None
