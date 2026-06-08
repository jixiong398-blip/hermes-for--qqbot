"""
Live2D WebSocket broadcast server.

Runs alongside the Hermes Gateway, broadcasting agent state changes
(thinking, tool_call, speaking, idle) and emotion/expression commands
to connected Live2D Electron windows.

Usage:
    from gateway.live2d_ws import get_live2d_ws_server

    server = get_live2d_ws_server(port=9190)
    await server.start()
    server.broadcast("state", state="thinking")
    server.broadcast("expression", name="soyo/smile04")
"""

import asyncio
import json
import logging
import threading
import time
import websockets
from websockets.asyncio.server import Server, ServerConnection
from typing import Optional, Set, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger("hermes.gateway.live2d_ws")


@dataclass
class Live2DWSServer:
    port: int = 9190
    host: str = "127.0.0.1"

    _connections: Set[ServerConnection] = field(default_factory=set, init=False)
    _server: Optional[Server] = field(default=None, init=False)
    _started: bool = field(default=False, init=False)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._server = await websockets.serve(
            self._handler,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=10,
            max_size=1024 * 1024,
        )
        logger.info(f"Live2D WS server listening on ws://{self.host}:{self.port}")

    async def stop(self) -> None:
        self._started = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for conn in list(self._connections):
            try:
                await conn.close()
            except Exception:
                pass
        self._connections.clear()

    async def _handler(self, websocket: ServerConnection) -> None:
        self._connections.add(websocket)
        peer = websocket.remote_address
        logger.debug(f"Live2D client connected: {peer}")
        try:
            async for message in websocket:
                # Re-broadcast messages from any client to all others.
                # This allows Hermes Gateway, external test scripts, and the LLM tool
                # to all send commands that reach the Live2D window.
                try:
                    data = json.loads(message)
                    await self._broadcast(data)
                except Exception:
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connections.discard(websocket)
            logger.debug(f"Live2D client disconnected: {peer}")

    async def _broadcast(self, data: Dict[str, Any]) -> None:
        if not self._connections:
            return
        payload = json.dumps(data, ensure_ascii=False)
        dead: Set[ServerConnection] = set()
        for conn in list(self._connections):
            try:
                await conn.send(payload)
            except websockets.exceptions.ConnectionClosed:
                dead.add(conn)
            except Exception:
                dead.add(conn)
        self._connections -= dead

    async def broadcast_state(self, state: str) -> None:
        await self._broadcast({"type": "state", "state": state})

    async def broadcast_expression(self, name: str) -> None:
        await self._broadcast({"type": "expression", "name": name})

    async def broadcast_motion(self, name: str) -> None:
        await self._broadcast({"type": "motion", "name": name})

    async def broadcast_emotion(self, emotion: str) -> None:
        await self._broadcast({"type": "emotion", "emotion": emotion})

    async def broadcast_speaking(self, state: bool) -> None:
        await self._broadcast({"type": "speaking", "state": state})


_server_instance: Optional[Live2DWSServer] = None
_server_lock = threading.Lock()


def get_live2d_ws_server(port: int = 9190) -> Live2DWSServer:
    global _server_instance
    with _server_lock:
        if _server_instance is None:
            _server_instance = Live2DWSServer(port=port)
        return _server_instance
