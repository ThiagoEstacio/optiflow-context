from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str    = "OptiFlow Context"
    APP_VERSION: str = "1.0.0"
    API_PREFIX: str  = "/api/v1"
    HOST: str        = "0.0.0.0"
    PORT: int        = 8200

    # TimescaleDB (mesmo banco do Historian, schema separado por tabelas)
    DATABASE_URL: str = "postgresql+asyncpg://historian:historian@timescaledb:5432/optiflow_historian"
    POOL_SIZE: int    = 5

    # Gateway (Connect) — consultado pelo Discovery
    GATEWAY_URL: str  = "http://gateway:8080"

    LOG_LEVEL: str    = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
