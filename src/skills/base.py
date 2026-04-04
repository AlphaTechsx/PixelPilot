import inspect
import logging
from typing import Any, Dict, Callable, Optional

class BaseSkill:
    """
    Base class for all agent skills.
    Handles common logic like enablement checks and dynamic method execution.
    """
    
    logger = logging.getLogger("pixelpilot.skills")
    name: str = "Base"

    def __init__(self):
        self.enabled = True
        self._methods: Dict[str, Callable] = {}
        self.logger.info(f"{self.name} Skill: Enabled")

    def register_method(self, name: str, func: Callable):
        """Register a method name to a function."""
        self._methods[name] = func

    def execute(self, method_name: str, kwargs: Dict[str, Any], desktop_manager=None) -> str:
        """
        Execute a skill method dynamically.
        Automatically injects 'desktop_manager' if the method expects it.
        """
        if not self.enabled:
            return f"{self.name} skill is disabled."

        if method_name not in self._methods:
            return f"Unknown method '{method_name}' for skill '{self.name}'"

        func = self._methods[method_name]

        try:
            sig = inspect.signature(func)
            call_args = kwargs.copy()
            
            if "desktop_manager" in sig.parameters:
                call_args["desktop_manager"] = desktop_manager
            
            valid_args = {
                k: v for k, v in call_args.items() 
                if k in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
            }

            return func(**valid_args)

        except Exception as e:
            return f"Error executing {method_name} in {self.name}: {e}"
