from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://erp_user:erp_pass@db:5432/heavy_erp"
    REDIS_URL: str = "redis://redis:6379"
    SECRET_KEY: str = "dev-secret"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24h
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"

settings = Settings()
