from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Attendance System API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Security
    SECRET_KEY: str # MUST be set in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    ADMIN_EMAIL: str = "admin@gbu.ac.in"
    ADMIN_PASSWORD: str = "GBU123"
    ADMIN_NAME: str = "System Administrator"
    
    # PostgreSQL Connection String
    # Format: postgresql+asyncpg://user:password@localhost:5432/dbname
    DATABASE_URL: str 

    class Config:
        env_file = ".env"

settings = Settings()