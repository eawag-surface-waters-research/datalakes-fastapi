import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import select

from app.auth import check_maintainer, check_member
from app.database import SessionDep, async_session_maker
from app.functions import extract_ssh_parts
from app.models import Repositories, RepositoriesBase

load_dotenv()

FILESYSTEM = os.getenv("FILESYSTEM")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["Repositories"])


@router.get("/")
async def get_all_repositories(session: SessionDep):
  """Get all repositories"""
  results = await session.exec(select(Repositories))
  return results.all()


@router.get("/{repositories_id}")
async def get_repository(repositories_id: int, session: SessionDep):
  """Get specific repository"""
  result = await session.exec(select(Repositories).where(Repositories.id == repositories_id))
  dataset = result.first()
  if not dataset:
    raise HTTPException(status_code=404, detail="Repository not found")
  return dataset


@router.post("/", status_code=202)
async def add_repository(
  repository_in: RepositoriesBase, background_tasks: BackgroundTasks, session: SessionDep, _: Annotated[dict, Depends(check_member)]
):
  """Create a new repository"""
  result = await session.exec(select(Repositories).where(Repositories.ssh == repository_in.ssh))
  existing_repo = result.one_or_none()
  if existing_repo:
    logger.info(f"Repository {repository_in.ssh} already in database")
    existing_repo.status = "updating"
    repo_id = existing_repo.id
    await session.commit()
  else:
    repository = Repositories.model_validate(repository_in)
    repository.status = "updating"
    session.add(repository)
    await session.commit()
    await session.refresh(repository)
    repo_id = repository.id

  ssh = extract_ssh_parts(repository_in.ssh)
  repo_path = Path(f"{FILESYSTEM}/git/{repo_id}/{ssh['name']}")
  if repo_path.exists():
    logger.info(f"Pulling repository {os.path.basename(repo_path)}")
    background_tasks.add_task(pull_repository, str(repo_path), repo_id)
    return {"id": repo_id, "status": "pull"}
  else:
    logger.info(f"Cloning repository {repository_in.ssh}")
    background_tasks.add_task(clone_repository, repository_in.ssh, str(repo_path), repo_id)
    return {"id": repo_id, "status": "clone"}


@router.delete("/{repositories_id}", status_code=204)
async def delete_repository(repositories_id: int, session: SessionDep, _: Annotated[dict, Depends(check_maintainer)]):
  """Delete a repository"""
  result = await session.exec(select(Repositories).where(Repositories.id == repositories_id))
  repository = result.first()

  if not repository:
    raise HTTPException(status_code=404, detail="Repository not found")

  logger.info(f"Deleting repository {repository.id}")
  ssh = extract_ssh_parts(repository.ssh)
  repo_path = Path(f"{FILESYSTEM}/git/{repository.id}/{ssh['name']}")
  try:
    shutil.rmtree(repo_path)
  except OSError as e:
    logger.error(f"Error deleting directory {repo_path}: {e}")
  await session.delete(repository)
  await session.commit()


async def pull_repository(repo_path: str, repo_id: int):
  """Pull repository"""
  async with async_session_maker() as session:
    result = await session.execute(select(Repositories).where(Repositories.id == repo_id))
    repo = result.scalar_one()
    try:
      proc = await asyncio.create_subprocess_exec("git", "-C", repo_path, "pull", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
      _, stderr = await proc.communicate()
      if proc.returncode != 0:
        raise RuntimeError(f"Git pull failed: {stderr.decode()}")
      repo.status = "success"
      logger.info("Pulling successful")
    except Exception:
      logger.exception(f"Error pulling repository {repo_id}")
      repo.status = "failed"
    finally:
      await session.commit()


async def clone_repository(ssh: str, repo_path: str, repo_id: int):
  """Clone repository"""
  async with async_session_maker() as session:
    result = await session.execute(select(Repositories).where(Repositories.id == repo_id))
    repo = result.scalar_one()
    try:
      proc = await asyncio.create_subprocess_exec("git", "clone", ssh, repo_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
      _, stderr = await proc.communicate()
      if proc.returncode != 0:
        raise RuntimeError(f"Git clone failed: {stderr.decode()}")
      repo.status = "success"
      logger.info("Cloning successful")
    except Exception:
      logger.exception(f"Error cloning repository {ssh}")
      repo.status = "failed"
      repo_path_obj = Path(repo_path)
      if repo_path_obj.exists():
        try:
          shutil.rmtree(repo_path_obj)
          logger.info(f"Cleaned up failed clone at {repo_path}")
        except OSError as cleanup_error:
          logger.error(f"Failed to cleanup directory {repo_path}: {cleanup_error}")
    finally:
      await session.commit()
