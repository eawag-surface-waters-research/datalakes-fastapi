from datetime import UTC, datetime, timedelta

from app.enums import DatasetKind, DatasetStatus, FileStatus
from app.models import Dataset, File

T0 = datetime(2024, 9, 1, tzinfo=UTC)


async def add_dataset(session, **values) -> Dataset:
  dataset = Dataset(title="Thermistor chain", **values)
  session.add(dataset)
  await session.commit()
  await session.refresh(dataset)
  return dataset


async def add_file(session, dataset_id, path, status=FileStatus.new, time_start=None, time_end=None, **values) -> File:
  file = File(dataset_id=dataset_id, path=path, size=1000, modified_at=T0, status=status, time_start=time_start, time_end=time_end, **values)
  session.add(file)
  await session.commit()
  await session.refresh(file)
  return file


async def add_processed_file(session, dataset_id, path, start: datetime, days: int = 30) -> File:
  return await add_file(session, dataset_id, path, status=FileStatus.ok, time_start=start, time_end=start + timedelta(days=days))


async def test_list_files_sorted_by_path(client, session):
  dataset = await add_dataset(session)
  other = await add_dataset(session)
  b = await add_file(session, dataset.id, "2024/b.nc")
  a = await add_file(session, dataset.id, "2024/a.nc")
  root = await add_file(session, dataset.id, "2023/z.nc")
  await add_file(session, other.id, "2024/a.nc")
  response = await client.get(f"/datasets/{dataset.id}/files")
  assert response.status_code == 200
  assert [file["id"] for file in response.json()] == [root.id, a.id, b.id]


async def test_list_files_of_draft_and_link_datasets(client, session):
  draft = await add_dataset(session, status=DatasetStatus.draft)
  file = await add_file(session, draft.id, "a.nc")
  link = await add_dataset(session, kind=DatasetKind.link, external_url="https://example.org")
  response = await client.get(f"/datasets/{draft.id}/files")
  assert response.status_code == 200
  assert [f["id"] for f in response.json()] == [file.id]
  response = await client.get(f"/datasets/{link.id}/files")
  assert response.status_code == 200
  assert response.json() == []
