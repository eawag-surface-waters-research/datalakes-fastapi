import pytest
from datetime import datetime, timezone
from httpx import ASGITransport, AsyncClient

from app.auth import check_member, check_dataset_permissions
from app.main import app

def override_check_member():
    return {"user_id": 1, "role": "member"}  # Mock user data

app.dependency_overrides[check_member] = override_check_member
app.dependency_overrides[check_dataset_permissions] = override_check_member

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_dataset_lifecycle():
    example_data = {
        "title": "Example Dataset",
        "description": "Example description",
        "owner": "Example Owner",
        "origin": "Measurement",
        "mapplot": "marker",
        "mapplotfunction": "",
        "datasource": "",
        "datasourcelink": "",
        "plotproperties": {},
        "citation": "",
        "downloads": 1,
        "fileconnect": "",
        "liveconnect": "",
        "renku": 1,
        "prefile": "",
        "prescript": "",
        "mindatetime": datetime(2021, 5, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "maxdatetime": datetime(2021, 5, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "mindepth": 0.0,
        "maxdepth": 0.0,
        "latitude": 0.0,
        "longitude": 0.0,
        "licenses_id": 1,
        "organisations_id": 1,
        "repositories_id": 1,
        "lakes_id": 1,
        "persons_id": 1,
        "projects_id": 1,
        "embargo": 1,
        "password": "",
        "accompanyingdata": "",
        "dataportal": None,
        "monitor": None
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/datasets/", json={})
        assert response.status_code == 201
        datasets_id = response.json()["id"]

        response = await ac.put(f"/datasets/{datasets_id}", json=example_data)
        assert response.status_code == 200
        data = response.json()
        for key, value in example_data.items():
            assert data[key] == value

        response = await ac.get(f"/datasets/{datasets_id}")
        assert response.status_code == 200
        data = response.json()
        for key, value in example_data.items():
            assert data[key] == value

        response = await ac.get("/datasets/")
        assert response.status_code == 200

        response = await ac.delete(f"/datasets/{datasets_id}")
        assert response.status_code == 204

