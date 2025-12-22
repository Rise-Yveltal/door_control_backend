"""
websocket_manager.py - WebSocket Manager (Simple version)
"""
import json
from typing import List
from fastapi import WebSocket

# WebSocket connections
active_connections: List[WebSocket] = []

async def broadcast_update(message: dict):
    """Broadcast message to all connected WebSocket clients"""
    disconnected = []
    for ws in list(active_connections):
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)
    
    for ws in disconnected:
        try:
            active_connections.remove(ws)
        except ValueError:
            pass

async def handle_websocket_connection(websocket: WebSocket, current_status: dict):
    """Handle new WebSocket connection"""
    await websocket.accept()
    active_connections.append(websocket)

    # Send initial status
    await websocket.send_json({
        "type": "initial_status",
        "current_status": current_status
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                parsed = json.loads(data)
                if isinstance(parsed, dict) and parsed.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                await websocket.send_json({"type": "pong"})
    except Exception:
        try:
            active_connections.remove(websocket)
        except ValueError:
            pass