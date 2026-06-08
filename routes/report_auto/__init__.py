from fastapi import APIRouter

# WebSocket-only deploy package.
# This router namespace is kept for compatibility if future REST routes are added.
api_v1 = APIRouter(prefix="/api/report_auto")
