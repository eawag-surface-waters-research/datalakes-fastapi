import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

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
