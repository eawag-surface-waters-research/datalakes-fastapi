from fastapi import APIRouter, Depends
from app.auth import validate_key

router = APIRouter(
    prefix="/datasetparameters",
    tags=["Dataset Parameters"]
)

@router.get("/{datasets_id}")
def list_dataset_parameters():
    return {"lakes": ["Lake Zurich", "Lake Geneva"]}

@router.post("/")
def post_dataset_parameters(key: str = Depends(validate_key)):
    return {"lakes": ["Lake Zurich", "Lake Geneva"]}

@router.put("/")
def put_dataset_parameters(key: str = Depends(validate_key)):
    return {"lakes": ["Lake Zurich", "Lake Geneva"]}

@router.delete("/")
def delete_dataset_parameters(key: str = Depends(validate_key)):
    return {"lakes": ["Lake Constance", "Lake Maggiore"]}