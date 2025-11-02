import pytest
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
async def test_datasetparameters_lifecycle():
    datasets_id = 0
    example_data = [
        {
            "datasets_id": datasets_id,
            "parameters_id": 0,
            "sensors_id": 0,
            "axis": "x",
            "parseparameter": "time",
            "unit": "seconds since 1970-01-01 00:00:00",
        },
        {
            "datasets_id": datasets_id,
            "parameters_id": 1,
            "sensors_id": 0,
            "axis": "y",
            "parseparameter": "temperature",
            "unit": "degC",
        },
        {
            "datasets_id": datasets_id,
            "parameters_id": 2,
            "sensors_id": 0,
            "axis": "y1",
            "parseparameter": "windspeed",
            "unit": "m/s",
            "detail": "2m"
        }
    ]
    update_data = {
            "datasets_id": datasets_id,
            "parameters_id": 3,
            "sensors_id": 0,
            "axis": "y1",
            "parseparameter": "winddirection",
            "unit": "deg",
            "detail": "2m"
        }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.delete(f"/datasetparameters/{datasets_id}")
        assert response.status_code == 204

        for data in example_data:
            response = await ac.post("/datasetparameters/", json=data)
            assert response.status_code == 201
            returned_data = response.json()
            for key, value in data.items():
                assert returned_data[key] == value

        response = await ac.put(f"/datasetparameters/{datasets_id}/{returned_data['id']}", json=update_data)
        assert response.status_code == 200
        edited_data = response.json()
        for key, value in update_data.items():
            assert edited_data[key] == value

        response = await ac.get(f"/datasetparameters/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= len(example_data)

        response = await ac.get(f"/datasetparameters/{datasets_id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == len(example_data)

        response = await ac.delete(f"/datasetparameters/{datasets_id}/{edited_data['id']}")
        assert response.status_code == 204

        response = await ac.delete(f"/datasetparameters/{datasets_id}")
        assert response.status_code == 204
