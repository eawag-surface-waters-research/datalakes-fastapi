from httpx import ASGITransport, AsyncClient
from dotenv import load_dotenv
from pathlib import Path
import pytest
import time
import os

from app.main import app
from app.functions import extract_ssh_parts
from app.auth import check_member, check_maintainer

load_dotenv()

FILESYSTEM = os.getenv("FILESYSTEM")

def override_check():
    return {"user_id": 1, "role": "member"}  # Mock user data

app.dependency_overrides[check_member] = override_check
app.dependency_overrides[check_maintainer] = override_check


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_repositories():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/repositories/")
    assert response.status_code == 200
    assert isinstance(response.json(), list)



@pytest.mark.anyio
async def test_add_repositories():
    repo_ssh = "git@github.com:LeXPLORE-Platform/Meteostation.git"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/repositories/")
        assert response.status_code == 200
        matches = [d for d in response.json() if d.get('ssh') == repo_ssh]
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
            elif status == "failure":
                raise ValueError("Something failed cloning the repository")
            elif status != "updating":
                raise ValueError("Incorrect values in the database")
            time.sleep(0.5)

        ssh = extract_ssh_parts(repo_ssh)
        repo_path = Path(f'{FILESYSTEM}/git/{repo_id}/{ssh["name"]}')
        assert repo_path.exists()
        response = await ac.delete(f"/repositories/{repo_id}")
        assert response.status_code == 204




