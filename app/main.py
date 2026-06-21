#app/main.py

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1 import auth
from app.api.v1.mobile import attendance as mobile_attendance
from app.api.v1.mobile import student as mobile_student
from app.api.v1.mobile import leaves as mobile_leaves
from fastapi.staticfiles import StaticFiles
from app.api.v1.web import management as web_management


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# CORS configuration - strict in production, open for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Change this to specific domains in production (e.g., ["https://admin.yourcollege.edu"])
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
    mobile_leaves.router, 
    prefix=f"{settings.API_V1_STR}/mobile/leaves", 
    tags=["Mobile Student API"]
)

app.include_router(
    web_management.router, 
    prefix=f"{settings.API_V1_STR}/web/management", 
    tags=["Web Admin API"]
)

@app.get("/")
def root():
    return {"message": "Attendance API is running"}