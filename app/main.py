import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.api.v1 import auth
from app.api.v1.mobile import attendance as mobile_attendance
from app.api.v1.mobile import student as mobile_student
from app.api.v1.web import management as web_management
from app.api.v1.web import corrections as web_corrections

# Import your admin initialization script
from app.create_admin import init_admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This runs exactly once when Uvicorn starts
    try:
        await init_admin()
    except Exception as e:
        print(f"Admin initialization skipped/failed: {e}")
    
    yield # Server runs here
    
    # This runs when Uvicorn shuts down (nothing to clean up for now)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan # Attach the startup event
)

# CORS configuration - strict in production, open for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("uploads", exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory="uploads"), name="uploads")

# Register Routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])

app.include_router(
    mobile_attendance.router,
    prefix=f"{settings.API_V1_STR}/mobile/attendance",
    tags=["Mobile API"]
)

app.include_router(
    mobile_student.router,
    prefix=f"{settings.API_V1_STR}/mobile/student",
    tags=["Mobile Student API"]
)

app.include_router(
    web_corrections.router,
    prefix=f"{settings.API_V1_STR}/web/corrections",
    tags=["Web Admin API"]
)

# Disabled for V1
# app.include_router(
#     mobile_leaves.router,
#     prefix=f"{settings.API_V1_STR}/mobile/leaves",
#     tags=["Mobile Student API"]
# )

app.include_router(
    web_management.router,
    prefix=f"{settings.API_V1_STR}/web/management",
    tags=["Web Admin API"]
)

@app.get("/")
def root():
    return {"message": "Attendance API is running"}