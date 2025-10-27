import asyncio
from fastapi import APIRouter, Path
from sqlmodel import select
from typing import Literal

from app.database import SessionDep
from app import models
from app.auth import check_permissions

router = APIRouter(
    prefix="/selectiontables",
    tags=["Selection Tables"]
)

TABLE_MODELS = {
    "parameters": models.Parameters,
    "lakes": models.Lakes,
    "organisations": models.Organisations,
    "persons": models.Persons,
    "projects": models.Projects,
    "sensors": models.Sensors,
    "licenses": models.Licenses,
}

TableName = Literal[tuple(TABLE_MODELS.keys())]

@router.get("/")
async def get_selection_tables(session: SessionDep):
    """Get all selection tables data"""
    response = {}
    for table_name, model in TABLE_MODELS.items():
        result = await session.exec(select(model))
        response[table_name] = result.all()
    response["axis"] = [{"name": "M"}, {"name": "x"}, {"name": "y"}, {"name": "z"}]
    return response


@router.get("/{table}")
async def get_table_data(
        session: SessionDep,
        table: TableName = Path(..., description="Table name")):
    """Get all rows from the specified table"""
    model = TABLE_MODELS[table]
    results = await session.exec(select(model))
    return results.all()