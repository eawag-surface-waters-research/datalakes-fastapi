from http import HTTPStatus
from typing import LiteralString

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_core import PydanticCustomError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorResponse(BaseModel):
  detail: str
  code: str


class ApiError(Exception):
  def __init__(self, status_code: int, code: str, detail: str):
    super().__init__(detail)
    self.status_code = status_code
    self.code = code
    self.detail = detail


def not_found(resource: str, resource_id: int | str) -> ApiError:
  return ApiError(404, "not_found", f"{resource} {resource_id} not found")


def error_response(status_code: int, code: str, detail: str) -> JSONResponse:
  return JSONResponse(status_code=status_code, content={"detail": detail, "code": code})


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
  return error_response(exc.status_code, exc.code, exc.detail)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
  """Framework errors such as an unknown route (404) or a wrong method (405)."""
  phrase = HTTPStatus(exc.status_code).phrase
  return error_response(exc.status_code, phrase.lower().replace(" ", "_"), str(exc.detail))


# Validation errors that keep their own code instead of `validation_error`, so clients can tell them apart. Raised with PydanticCustomError.
OWN_CODE_ERRORS = {"unknown_option"}


def invalid(detail: str, error_type: LiteralString = "value_error") -> PydanticCustomError:
  """A validation error with the message as it is, without pydantic's `Value error, ` prefix."""
  return PydanticCustomError(error_type, "{detail}", {"detail": detail})


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
  """One message that names each invalid field, e.g. `title: String should have at least 1 character`."""
  for error in exc.errors():
    if error["type"] in OWN_CODE_ERRORS:
      return error_response(422, error["type"], error["msg"])
  messages = []
  for error in exc.errors():
    field = ".".join(str(part) for part in error["loc"][1:])  # the first part is where it was sent: body, query or path
    messages.append(f"{field}: {error['msg']}" if field else error["msg"])
  return error_response(422, "validation_error", "; ".join(messages))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
  """Anything unexpected, e.g. a database error. The cause is only logged, never sent to the client."""
  return error_response(
    500,
    "internal_error",
    "An unexpected server error occurred. The developer has been notified, for updates on bug fixes please contact James Runnalls (james.runnalls@eawag.ch).",
  )


def add_exception_handlers(app: FastAPI) -> None:
  app.add_exception_handler(ApiError, api_error_handler)
  app.add_exception_handler(Exception, unhandled_error_handler)
  app.add_exception_handler(StarletteHTTPException, http_exception_handler)
  app.add_exception_handler(RequestValidationError, validation_error_handler)
