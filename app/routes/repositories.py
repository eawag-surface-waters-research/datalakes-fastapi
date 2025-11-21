from fastapi import APIRouter, BackgroundTasks
from sqlmodel import select
from pathlib import Path
import os

from app.database import SessionDep
from app.models import Repositories, RepositoriesBase
from app.auth import check_member
from app.functions import extract_ssh_parts

from dotenv import load_dotenv

load_dotenv()

FILESYSTEM = os.getenv("FILESYSTEM")

router = APIRouter(
    prefix="/repositories",
    tags=["Repositories"]
)

@router.get("/")
async def get_repositories(session: SessionDep):
    """Get all repositories"""
    results = await session.exec(select(Repositories))
    return results.all()

@router.post("/", status_code=201)
async def add_repository(
        repository_in: RepositoriesBase,
        background_tasks: BackgroundTasks,
        session: SessionDep,
        _: dict = Depends(check_member)):
    """Create a new repository"""
    existing_repo = await session.execute(select(Repository).where(Repository.ssh == repository_in.ssh))
    if existing_repo.scalar_one_or_none():
        logging.info(f"Repository {repository_in.ssh} already in database")
        repo_id = existing_repo.id
    else:
        repository = Repositories.model_validate(repository_in)
        session.add(repository)
        await session.commit()
        await session.refresh(repository)
        repo_id = repository.id

    ssh = extract_ssh_parts(repository_in.ssh)
    repo_path = Path(f'{FILESYSTEM}/git/{repo_id}/{ssh["name"]}')
    if repo_path.exists():
        background_tasks.add_task(pull_repository, str(repo_path), session)
        return {"id": repo_id, "status": "pull"}
    else:
        background_tasks.add_task(clone_repository, repository_in.ssh, str(repo_path), session)
        return {"id": repo_id, "status": "clone"}

def clone_repository(ssh_url: str, repo_path: str, session):
    logging.info(f"Cloning repository {ssh_url}")


def pull_repository(repo_path: str, session):
    logging.info(f"Pulling repository {os.path.basename(repo_path)}")





