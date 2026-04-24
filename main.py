from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import uvicorn

from routes.report_auto import api_v1
from routes.report_auto.autoreport_websocket import router as report_ws_router

# Load environment variables
load_dotenv()

app = FastAPI()

# Enable CORS
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "*")
if allowed_origins_env == "*":
    origins = ["*"]
else:
    origins = [origin.strip() for origin in allowed_origins_env.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(api_v1)
app.include_router(report_ws_router)


@app.get("/")
async def health_check():
    return {"status": "success", "message": "FastAPI is running"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    # Listen on 0.0.0.0 to allow external access
    uvicorn.run("main:app", host="0.0.0.0", port=port)
