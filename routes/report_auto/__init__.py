from fastapi import APIRouter
from .api_auto_report import router as report_router

# Create an APIRouter instance
api_v1 = APIRouter(prefix="/api/report_auto")

# Include the routers from modules
api_v1.include_router(report_router)
