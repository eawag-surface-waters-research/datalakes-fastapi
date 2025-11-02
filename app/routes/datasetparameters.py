from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import select, delete

from app.database import SessionDep
from app.models import Datasetparameters, DatasetparametersBase
from app.auth import check_member, check_dataset_permissions

router = APIRouter(
    prefix="/datasetparameters",
    tags=["Dataset Parameters"]
)


@router.get("/")
async def get_all_datasetparameters(session: SessionDep):
    """Get all dataset parameters"""
    result = await session.exec(select(Datasetparameters))
    return result.all()


@router.get("/{datasets_id}")
async def get_dataset_datasetparameters(datasets_id: int, session: SessionDep):
    """Get specific dataset"""
    result = await session.exec(
        select(Datasetparameters).where(Datasetparameters.datasets_id == datasets_id)
    )
    dataset = result.all()

    if len(dataset) == 0:
        raise HTTPException(status_code=404, detail="Dataset not found")

    return dataset


@router.post("/", status_code=201)
async def create_datasetparameter(
        dataset_in: DatasetparametersBase,
        session: SessionDep,
        _: dict = Depends(check_member)
):
    """Create a new dataset"""
    dataset = Datasetparameters.model_validate(dataset_in)
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


@router.put("/{datasets_id}/{datasetparameters_id}", status_code=200)
async def overwrite_datasetparameter(
        datasets_id: int,
        datasetparameters_id: int,
        dataset_in: DatasetparametersBase,
        session: SessionDep,
        _: dict = Depends(check_dataset_permissions)
):
    """Overwrite an existing dataset parameter"""
    existing = await session.get(Datasetparameters, datasetparameters_id)

    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dataset parameter with id {datasetparameters_id} not found"
        )

    if existing.datasets_id != datasets_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dataset parameter with id {datasetparameters_id} does not belong to dataset with id {datasets_id}"
        )

    for key, value in dataset_in.model_dump(exclude_unset=True).items():
        setattr(existing, key, value)

    await session.commit()
    await session.refresh(existing)
    return existing


@router.delete("/{datasets_id}", status_code=204)
async def delete_dataset_dataparameters(
        datasets_id: int,
        session: SessionDep,
        _: dict = Depends(check_dataset_permissions)
):
    """Delete all datasetparmeters for a dataset"""
    # Execute a bulk delete
    result = await session.exec(
        delete(Datasetparameters).where(Datasetparameters.datasets_id == datasets_id)
    )

    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="No dataset parameters found for the given dataset"
        )

    await session.commit()
    return None

@router.delete("/{datasets_id}/{datasetparameters_id}", status_code=204)
async def delete_dataparameter(
        datasets_id: int,
        datasetparameters_id: int,
        session: SessionDep,
        _: dict = Depends(check_dataset_permissions)
):
    """Delete a datasetparameter"""
    result = await session.exec(
        select(Datasetparameters)
        .where(Datasetparameters.id == datasetparameters_id)
        .where(Datasetparameters.datasets_id == datasets_id)
    )
    dataset = result.first()

    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    await session.delete(dataset)
    await session.commit()
    return None
