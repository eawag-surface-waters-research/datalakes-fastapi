from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.database import SessionDep
from app.models import MaintenanceCreate, MaintenanceUpdate, Maintenance

router = APIRouter(
    prefix="/maintenance",
    tags=["Maintenance"]
)

@router.get("/{maintenance_id}")
async def get_maintenance(maintenance_id: int, session: SessionDep):
    """Get maintenance"""
    existing = await session.get(Maintenance, maintenance_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Maintenance not found")
    return existing

@router.get("/{datasets_id}")
async def get_dataset_maintenance(datasets_id: int, session: SessionDep):
    """Get all maintenance for a dataset"""
    result = await session.exec(
        select(Maintenance).where(Maintenance.datasets_id == datasets_id)
    )
    return result.all()

@router.post("/", status_code=201)
async def create_maintenance(
        maintenance_in: MaintenanceCreate,
        session: SessionDep
):
    """Create a new maintenance"""
    maintenance = Maintenance.model_validate(maintenance_in)
    session.add(maintenance)
    await session.commit()
    await session.refresh(maintenance)
    return maintenance


@router.patch("/{maintenance_id}", status_code=200)
async def update_maintenance(
        maintenance_id: int,
        maintenance_in: MaintenanceUpdate,
        session: SessionDep
):
    """Update an existing maintenance"""
    existing = await session.get(Maintenance, maintenance_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Maintenance with id {maintenance_id} not found"
        )
    maintenance_data = maintenance_in.model_dump(exclude_unset=True)
    for key, value in maintenance_data.items():
        setattr(existing, key, value)
    session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return existing


@router.delete("/{maintenance_id}", status_code=204)
async def delete_maintenance(maintenance_id: int, session: SessionDep):
    """Delete a maintenance"""
    existing = await session.get(Maintenance, maintenance_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Maintenance not found")
    await session.delete(existing)
    await session.commit()
    return None
