import asyncio
import os
import subprocess
from pathlib import Path

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

from app.auth import check_maintainer, check_member
from app.functions import extract_ssh_parts
from app.main import app

load_dotenv()

FILESYSTEM = os.getenv("FILESYSTEM")
REPO_SSH = "git@github.com:LeXPLORE-Platform/Meteostation.git"


def github_ssh_available() -> bool:
  """Cloning over SSH needs a GitHub SSH key and outbound port 22."""
  try:
    result = subprocess.run(
      ["git", "ls-remote", REPO_SSH, "HEAD"],
      check=False,
      capture_output=True,
      timeout=15,
      env={**os.environ, "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"},
    )
  except subprocess.TimeoutExpired, OSError:
    return False
  return result.returncode == 0


def override_check():
  return {"user_id": 1, "role": "member"}  # Mock user data


app.dependency_overrides[check_member] = override_check
app.dependency_overrides[check_maintainer] = override_check


@pytest.fixture(scope="session")
def anyio_backend():
  return "asyncio"


@pytest.mark.anyio
async def test_repositories():
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
    response = await ac.get("/repositories/")
  assert response.status_code == 200
  assert isinstance(response.json(), list)


@pytest.mark.anyio
@pytest.mark.skipif(not github_ssh_available(), reason="GitHub is not reachable over SSH (no SSH key or port 22 blocked)")
async def test_add_repositories():
  repo_ssh = REPO_SSH
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
    response = await ac.get("/repositories/")
    assert response.status_code == 200
    matches = [d for d in response.json() if d.get("ssh") == repo_ssh]
    if len(matches) == 1:
      await ac.delete(f"/repositories/{matches[0]['id']}")

    response = await ac.post("/repositories/", json={"ssh": repo_ssh})
    assert response.status_code == 202
    repo_id = response.json()["id"]
    for i in range(100):
      response = await ac.get(f"/repositories/{repo_id}")
      assert response.status_code == 200
      status = response.json()["status"]
      if status == "success":
        break
      elif status == "failed":
        raise ValueError("Something failed cloning the repository")
      elif status != "updating":
        raise ValueError("Incorrect values in the database")
      await asyncio.sleep(0.5)

    ssh = extract_ssh_parts(repo_ssh)
    repo_path = Path(f"{FILESYSTEM}/git/{repo_id}/{ssh['name']}")
    assert repo_path.exists()
    response = await ac.delete(f"/repositories/{repo_id}")
    assert response.status_code == 204
