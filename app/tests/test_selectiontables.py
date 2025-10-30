import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.routes.selectiontables import TABLE_MODELS
from app.auth import check_member
from app.main import app

def override_check_member():
    return {"user_id": 1, "role": "member"}  # Mock user data

app.dependency_overrides[check_member] = override_check_member

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_selectiontables():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/selectiontables/")
    assert response.status_code == 200
    for key in TABLE_MODELS:
        assert key in response.json(), f"Expected key '{key}' not found in response"


@pytest.mark.anyio
async def test_create_table_row():
    """Test creating a new row in a table"""
    table_name = "lakes"
    test_data = {
        "name": "Murtensee",
        "elevation": 1003,
    }
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            f"/selectiontables/{table_name}",
            json=test_data
        )
    print(response)
    assert response.status_code == 201
    data = response.json()
    for key, value in test_data.items():
        assert data[key] == value


@pytest.mark.anyio
async def test_create_table_row_invalid_data():
    """Test creating a row with invalid data returns 422"""
    table_name = "lakes"
    invalid_data = {
        "name": "Greifensee",
        "depth": "1m"  # should be int
    }
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            f"/selectiontables/{table_name}",
            json=invalid_data
        )
    print(response)
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


@pytest.mark.anyio
async def test_create_table_row_invalid_table():
    """Test creating a row in non-existent table"""
    invalid_table = "fake"
    test_data = {
        "name": "test"
    }
    async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            f"/selectiontables/{invalid_table}",
            json=test_data
        )
    assert response.status_code in [404, 422]