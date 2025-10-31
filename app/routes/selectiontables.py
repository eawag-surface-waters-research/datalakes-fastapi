from fastapi import APIRouter, Path, HTTPException, status, Depends
from sqlmodel import select
from typing import Literal, Dict, Any
from pydantic import ValidationError

from app.database import SessionDep
from app import models
from app.auth import check_member

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
async def get_all_selection_tables(session: SessionDep):
    """Get all selection tables data"""
    response = {}
    for table_name, model in TABLE_MODELS.items():
        result = await session.exec(select(model))
        response[table_name] = result.all()
    response["axis"] = [{"name": "M"}, {"name": "x"}, {"name": "y"}, {"name": "z"}]
    return response


@router.get("/{table}")
async def get_selection_table(
        session: SessionDep,
        table: TableName = Path(..., description="Table name")):
    """Get all rows from the specified table"""
    model = TABLE_MODELS[table]
    results = await session.exec(select(model))
    return results.all()


@router.post("/{table}", status_code=status.HTTP_201_CREATED)
async def create_section_table_row(
        data: Dict[str, Any],
        session: SessionDep,
        table: TableName = Path(..., description="Table name"),
        _: dict = Depends(check_member)):
    """Create a new row in the specified table"""
    model = TABLE_MODELS[table]

    try:
        new_row = model.model_validate(data)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.errors()
        )

    session.add(new_row)
    await session.commit()
    await session.refresh(new_row)

    return new_row
