from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import asyncio
import configparser
import io
import os
import re
import zipfile
import aiohttp
from pathlib import Path

router = APIRouter(
    prefix="/remote",
    tags=["Remote"],
)

RCLONE_CONFIG_PATH = os.getenv("RCLONE_CONFIG_PATH", str(Path.home() / ".config" / "rclone" / "rclone.conf"))

_VALID_REMOTE_NAME = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
_VALID_PREFIX = re.compile(r'^[a-zA-Z0-9_./ -]{0,500}$')


class RcloneNewRequest(BaseModel):
    dataset_id: str
    config: Dict[str, str]
    prefix: str


@router.post("/rclone/new")
async def add_rclone_remote(request: RcloneNewRequest) -> List[str]:
    """Add a new rclone remote and return a list of files at the given prefix."""
    if not _VALID_REMOTE_NAME.match(request.dataset_id):
        raise HTTPException(status_code=422, detail="Invalid remote name: use only alphanumeric, hyphens, underscores (max 64 chars)")

    if not _VALID_PREFIX.match(request.prefix):
        raise HTTPException(status_code=422, detail="Invalid prefix: use only alphanumeric, dots, slashes, hyphens, underscores, spaces (max 500 chars)")

    if ".." in request.prefix:
        raise HTTPException(status_code=422, detail="Prefix must not contain path traversal")

    _write_rclone_config(request.dataset_id, request.config)

    remote_path = f"{request.dataset_id}:{request.prefix}"
    files = await _rclone_list(remote_path)
    return files


class LinkRequest(BaseModel):
    dataset_id: str
    link: str


@router.post("/link")
async def download_and_list_link(request: LinkRequest) -> List[str]:
    """Download a zip from a URL, extract it into /data/datasets/<dataset_id>, and return the file list."""
    if not _VALID_REMOTE_NAME.match(request.dataset_id):
        raise HTTPException(status_code=422, detail="Invalid dataset_id: use only alphanumeric, hyphens, underscores (max 64 chars)")

    async with aiohttp.ClientSession() as session:
        async with session.get(request.link) as response:
            if response.status != 200:
                raise HTTPException(status_code=502, detail=f"Failed to download link: HTTP {response.status}")
            data = await response.read()

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="Downloaded file is not a valid zip archive")

    dest = Path("/data/datasets") / request.dataset_id
    dest.mkdir(parents=True, exist_ok=True)

    with zf:
        zf.extractall(dest)
        return zf.namelist()


def _write_rclone_config(name: str, config: Dict[str, str]) -> None:
    config_path = Path(RCLONE_CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    if config_path.exists():
        parser.read(config_path)

    parser[name] = config

    with open(config_path, "w") as f:
        parser.write(f)


async def _rclone_list(remote_path: str) -> List[str]:
    proc = await asyncio.create_subprocess_exec(
        "rclone", "lsf", "--recursive", remote_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"rclone failed: {stderr.decode().strip()}"
        )

    lines = stdout.decode().splitlines()
    return [line for line in lines if line.strip()]
