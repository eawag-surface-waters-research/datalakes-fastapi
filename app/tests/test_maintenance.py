import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_maintenance_lifecycle():
    datasets_id = 0
    example_issue = {
        "datasets_id": datasets_id,
        "parameters_id": 0,
        "datasetparameters_id": 0,
        "depths": "6-10",
        "description": "Some description",
        "starttime": "2025-09-01T07:07:28.796000Z",
        "endtime": "2025-11-02T07:07:28.796000Z",
        "reporter": "Jeff",
        "state": "reported"
        }
    update = {
        "state": "confirmed"
        }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post("/maintenance/", json=example_issue)
        assert response.status_code == 201

        maintenance_id = response.json()['id']

        response = await ac.get(f"/maintenance/dataset/{datasets_id}")
        assert response.status_code == 200
        assert type(response.json()) == list

        response = await ac.get(f"/maintenance/{maintenance_id}")
        assert response.status_code == 200

        response = await ac.patch(f"/maintenance/{maintenance_id}", json=update)
        assert response.status_code == 200
        for key in update:
            assert response.json()[key] == update[key]
        for key in example_issue:
            if key not in update:
                assert response.json()[key] == example_issue[key]

        response = await ac.delete(f"/maintenance/{maintenance_id}")
        assert response.status_code == 204
