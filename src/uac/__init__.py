"""UAC helper package (moved from root uac_* scripts).

This package contains the UAC agent and orchestrator for Secure Desktop handling.
"""

from .detection import (
    get_process_uac_state,
    get_uac_prompt_state,
    get_uac_state_snapshot,
    is_uac_enabled,
)
from .approval import ask_uac_brain, confirm_uac_allow, resolve_uac_allow_decision
from .flow import (
    get_uac_poll_interval_seconds,
    get_external_uac_mode,
    get_uac_flow_progress,
    get_uac_queue_gate,
    handle_uac_prompt_blocking,
    set_external_uac_mode,
    submit_uac_decision,
    wait_for_uac_mode_clear,
)

__all__ = [
    "agent",
    "orchestrator",
    "is_uac_enabled",
    "get_process_uac_state",
    "get_uac_prompt_state",
    "get_uac_state_snapshot",
    "ask_uac_brain",
    "confirm_uac_allow",
    "resolve_uac_allow_decision",
    "get_uac_poll_interval_seconds",
    "get_external_uac_mode",
    "get_uac_flow_progress",
    "get_uac_queue_gate",
    "handle_uac_prompt_blocking",
    "set_external_uac_mode",
    "submit_uac_decision",
    "wait_for_uac_mode_clear",
]
