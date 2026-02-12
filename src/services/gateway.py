import logging
import asyncio
import json
import websockets
import os
from typing import Optional
from agent.core import AgentOrchestrator
from config import Config

logger = logging.getLogger("pixelpilot.gateway")


class GatewayServer:
    def __init__(self, agent: AgentOrchestrator, host="localhost", port=8765, auth_token=None):
        self.agent = agent
        self.host = host
        self.port = port
        self.auth_token = auth_token or os.environ.get(
            "PIXELPILOT_GATEWAY_TOKEN", "pixelpilot-secret"
        )
        self._server = None
        self._shutdown_event = asyncio.Event()

    async def _execute_agent_task(self, command: str) -> bool:
        """Run agent task in a thread to avoid blocking the async loop."""
        return await asyncio.to_thread(self.agent.run_task, command)

    async def handler(self, websocket):
        client_info = websocket.remote_address
        logger.info(f"Gateway client connected: {client_info}")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "Invalid JSON"}))
                    continue

                # Auth check
                if self.auth_token and data.get("auth") != self.auth_token:
                    logger.warning(f"Unauthorized gateway access attempt from {client_info}")
                    await websocket.send(json.dumps({"error": "Unauthorized"}))
                    continue

                command = data.get("command")
                params = data.get("params", {})
                
                if not command:
                    await websocket.send(json.dumps({"error": "No command provided"}))
                    continue

                full_command = command
                if params:
                    full_command += " " + " ".join(f"{k}: {v}" for k, v in params.items())

                logger.info(f"Gateway executing: {full_command}")
                
                try:
                    result = await self._execute_agent_task(full_command)
                    
                    last_output = "Task completed."
                    if self.agent.task_history:
                        last_action = self.agent.task_history[-1]
                        if last_action.get("action_type") == "reply":
                            last_output = last_action.get("params", {}).get("text", "")
                        else:
                            last_output = last_action.get("reasoning", str(last_action))

                    response = {
                        "status": "success" if result else "failed",
                        "result": result,
                        "output": last_output,
                        "params": params
                    }
                    await websocket.send(json.dumps(response))
                    
                except Exception as e:
                    logger.error(f"Gateway execution error: {e}")
                    await websocket.send(json.dumps({"error": f"Execution failed: {str(e)}"}))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Gateway client disconnected: {client_info}")
        except Exception as e:
            logger.error(f"Gateway handler error: {e}")

    async def serve(self):
        """Async entry point to run the server."""
        logger.info(f"Starting Gateway Server on ws://{self.host}:{self.port}")
        async with websockets.serve(self.handler, self.host, self.port):
            await self._shutdown_event.wait()

    def start(self):
        """Blocking start method for standalone usage."""
        try:
            asyncio.run(self.serve())
        except KeyboardInterrupt:
            logger.info("Gateway server stopped by user")
        except Exception as e:
            logger.error(f"Gateway server failed to start: {e}")

    def stop(self):
        self._shutdown_event.set()
