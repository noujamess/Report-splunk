from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List

ws_router = APIRouter(prefix="/ws")

# Simple list to store active connections
active_connections: List[WebSocket] = []

@ws_router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    print(f"Client connected. Total connections: {len(active_connections)}")
    
    try:
        while True:
            # Keep connection open
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        print(f"Client disconnected. Total connections: {len(active_connections)}")
    except Exception as e:
        print(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)
