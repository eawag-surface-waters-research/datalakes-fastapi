from fastapi import APIRouter, Depends
from app.auth import check_permissions

from app.database import SessionDep


router = APIRouter(
    prefix="/datasetparameters",
    tags=["Dataset Parameters"]
)

@router.get("/{datasets_id}")
def list_dataset_parameters(datasets_id: str):
    return {"datasets_id": datasets_id}

@router.post("/{datasets_id}")
def post_dataset_parameters(datasets_id: str, permissions: None = Depends(check_permissions)):
    return {"datasets_id": datasets_id}

@router.put("/{datasets_id}")
def put_dataset_parameters(datasets_id: str, permissions: None = Depends(check_permissions)):
    return {"datasets_id": datasets_id}

@router.delete("/{datasets_id}")
def delete_dataset_parameters(datasets_id: str, permissions: None = Depends(check_permissions)):
    return {"datasets_id": datasets_id}
