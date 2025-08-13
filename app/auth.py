from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from dotenv import load_dotenv
import os

load_dotenv()
header_scheme = APIKeyHeader(name="x-key")

def validate_key(key: str = Depends(header_scheme)):
    if key != os.getenv("API_KEY"):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key