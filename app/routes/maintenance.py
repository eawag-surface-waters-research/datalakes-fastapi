from fastapi import APIRouter, HTTPException, Depends, status
from sqlmodel import select, delete

from app.database import SessionDep
from app.models import Datasetparameters, DatasetparametersBase
from app.auth import check_member, check_dataset_permissions

router = APIRouter(
    prefix="/maintenance",
    tags=["Maintenance"]
)

