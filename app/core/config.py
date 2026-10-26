from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Attendance System API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str # MUST be set in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 15 # 15 days
    REFRESH_TOKEN_EXPIRE_DAYS: int = 60 # 60 days (sliding session on refresh)
    
    # Initial Admin Credentials (MUST be set in .env)
    ADMIN_EMAIL: str
    ADMIN_PASSWORD: str
    ADMIN_NAME: str = "System Administrator"
    
    # PostgreSQL Connection String (MUST be set in .env)
    DATABASE_URL: str 

    # University Timetable & Schedule Integration (MUST be set in .env)
    SAMAY_API_URL: str
    MYGBU_SCHEDULE_URL: str

    # Push Notification Settings
    NOTIFICATION_PROVIDER: str = "ntfy"
    NTFY_SERVER_URL: str = "http://192.168.1.7:8095"

    class Config:
        env_file = ".env"

settings = Settings()
