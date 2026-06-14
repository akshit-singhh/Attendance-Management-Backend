from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1 import auth
from app.api.v1.mobile import attendance as mobile_attendance


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

# Register Routers
app.include_router(auth.router, prefix=f"{settings.API_V1_STR}/auth", tags=["Authentication"])

app.include_router(
    mobile_attendance.router, 
    prefix=f"{settings.API_V1_STR}/mobile/attendance", 
    tags=["Mobile API"]
)

@app.get("/")
def root():
    return {"message": "Attendance API is running"}