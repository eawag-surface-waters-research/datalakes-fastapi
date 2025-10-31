from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import select

from app.database import SessionDep
from app.models import Datasets
from app.auth import check_member, check_dataset_permissions
from app.functions import convert_string_datetimes

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
            Datasets.data_portal.is_not(None)
        )
    )
    return result.all()

@router.get("/{datasets_id}")
async def get_dataset(datasets_id: int, session: SessionDep):
    """Get specific dataset"""
    result = await session.exec(
        select(Datasets).where(Datasets.id == datasets_id)
    )
    dataset = result.first()

    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    return dataset

@router.post("/", status_code=201)
async def create_dataset(
        dataset: Datasets,
        session: SessionDep,
        _: dict = Depends(check_member)
):
    """Create a new dataset"""
    convert_string_datetimes(dataset, "mindatetime", "maxdatetime")

    if dataset.id is not None:
        existing = await session.get(Datasets, dataset.id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Dataset with id {dataset.id} already exists."
            )

    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.put("/{datasets_id}")
async def update_dataset(
        datasets_id: int,
        dataset_update: Datasets,
        session: SessionDep,
        _: dict = Depends(check_dataset_permissions)
):
    """Update an existing dataset"""
    result = await session.exec(
        select(Datasets).where(Datasets.id == datasets_id)
    )
    dataset = result.first()

    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    for key, value in dataset_update.model_dump(exclude_unset=True).items():
        setattr(dataset, key, value)

    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


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
