from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import select

from app.database import SessionDep
from app.models import Datasets, DatasetsCreate, DatasetsUpdate
from app.auth import check_member, check_dataset_permissions

router = APIRouter(
    prefix="/datasets",
    tags=["Datasets"]
)

@router.get("/")
async def get_all_datasets(session: SessionDep):
    """Get all datasets"""
    result = await session.exec(
        select(Datasets).where(
            Datasets.title.is_not(None),
            Datasets.dataportal.is_not(None)
        )
    )
    return result.all()

@router.get("/{datasets_id}")
async def get_dataset(datasets_id: int, session: SessionDep):
    """Get specific dataset"""
    existing = await session.get(Datasets, datasets_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return existing

@router.post("/", status_code=201)
async def create_dataset(
        dataset_in: DatasetsCreate,
        session: SessionDep,
        _: dict = Depends(check_member)
):
    """Create a new dataset"""
    if dataset_in.id is not None:
        existing = await session.get(Datasets, dataset_in.id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dataset with id {dataset_in.id} already exists."
            )

    dataset = Datasets.model_validate(dataset_in)
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.patch("/{dataset_id}", status_code=200)
async def update_dataset(
        dataset_id: int,
        dataset_in: DatasetsUpdate,
        session: SessionDep,
        _: dict = Depends(check_dataset_permissions)
):
    """Update an existing dataset"""
    existing = await session.get(Datasets, dataset_id)

    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset with id {dataset_id} not found"
        )

    dataset_data = dataset_in.model_dump(exclude_unset=True)
    for key, value in dataset_data.items():
        setattr(existing, key, value)

    session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


@router.delete("/{datasets_id}", status_code=204)
async def delete_dataset(datasets_id: int, session: SessionDep, _: dict = Depends(check_dataset_permissions)):
    """Delete a dataset"""
    result = await session.exec(
        select(Datasets).where(Datasets.id == datasets_id)
    )
    dataset = result.first()

    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    await session.delete(dataset)
    await session.commit()
    return None
