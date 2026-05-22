"""
OptiFlow Context — Serviço de hierarquia e ontologia de ativos industriais.

Responsabilidade única: "saber o que cada ativo é e como se relaciona."

Vision, OPERA e qualquer módulo que precise de contexto de ativo
consultam este serviço — não implementam lógica de hierarquia própria.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.api.routes import router
from app.db.session import init_schema

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("context.starting")
    try:
        await init_schema()
        log.info("context.schema_ready")
    except Exception as e:
        log.error("context.schema_failed error=%s", e)
        raise
    yield
    log.info("context.stopped")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Asset hierarchy, ontology and relations — single source of truth for all modules",
    openapi_url=f"{settings.API_PREFIX}/openapi.json",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION}


@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy", "version": settings.APP_VERSION})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT)
