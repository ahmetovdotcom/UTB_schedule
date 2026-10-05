"""Owner-only reporting proxy. Never expose the internal reporting key to JS."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from auth import AdminGrant, User, require_user
from database.models import get_session
from publication import destination


async def require_admin(user: User = Depends(require_user), db: AsyncSession = Depends(get_session)):
    if user is None or await db.get(AdminGrant, user.id) is None:
        raise HTTPException(403, "Статистика доступна только администратору")
    return user


router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/analytics")
async def analytics(response: Response, days: int = Query(30, ge=1, le=180)):
    url, key = destination()
    url = url.rsplit("/", 1)[0] + "/analytics/summary"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            result = await client.get(url, params={"days": days}, headers={"Authorization": f"Bearer {key}"})
            result.raise_for_status()
            data = result.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Не удалось получить статистику. Проверьте доступность приложения для просмотра расписания.") from exc
    response.headers["Cache-Control"] = "no-store"
    return data
