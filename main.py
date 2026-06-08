from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import uvicorn

from routes.report_auto import api_v1
from routes.report_auto.api_manage_queries import router as manage_queries_router
from routes.report_auto.autoreport_websocket import router as report_ws_router

load_dotenv()

app = FastAPI(
    title="Project Report Deploy API",
    version="1.0.0",
    description="Deployable report service for Splunk and ELK report generation.",
)

allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "*")
if allowed_origins_env == "*":
    origins = ["*"]
    origin_regex = None
else:
    origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
    origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1)
app.include_router(report_ws_router)
app.include_router(manage_queries_router, prefix="/api/manage/queries", tags=["Manage Queries"])


@app.get("/")
async def health_check():
    return {
        "status": "success",
        "message": "Project Report Deploy API is running",
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    reload_enabled = os.getenv("UVICORN_RELOAD", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload_enabled)
