from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine

# Импортируем модульные роутеры
from app.routers.analytics import router as analytics_router
from app.routers.teachers import router as teachers_router
from app.routers.groups import router as groups_router
from app.routers.publication import router as publication_router
from app.routers.schedules import router as schedules_router

# Импортируем модели обязательно, чтобы Base знал о них!
import app.models.models  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- СОБЫТИЕ ПРИ СТАРТЕ ПРИЛОЖЕНИЯ ---
    async with engine.begin() as conn:
        # Создает таблицы в БД, если их еще нет
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    
    # --- СОБЫТИЕ ПРИ ОСТАНОВКЕ ---
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Accept"]


)


api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(analytics_router)
api_v1_router.include_router(teachers_router)
api_v1_router.include_router(groups_router)
api_v1_router.include_router(publication_router)
api_v1_router.include_router(schedules_router)

app.include_router(api_v1_router)






@app.middleware("http")
async def prevent_stale_api_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response
