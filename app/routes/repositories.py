from fastapi import APIRouter
from sqlmodel import select

from app.database import SessionDep
from app.models import Repositories

router = APIRouter(
    prefix="/repositories",
    tags=["Respositories"]
)

@router.get("/")
async def get_repositories(session: SessionDep):
    """Get all repositories"""
    results = await session.exec(select(Repositories))
    return results.all()
