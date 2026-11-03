import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture(scope="session")
def anyio_backend():
  return "asyncio"


@pytest.mark.anyio
async def test_welcome():
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    response = await client.get("/")
  assert response.status_code == 200
