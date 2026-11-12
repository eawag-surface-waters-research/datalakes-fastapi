"""
The FastAPI application and its startup/shutdown logic
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.database import DATABASE_URL, check_db_connection, engine, get_safe_db_url
from app.errors import add_exception_handlers
from app.observability import setup_observability
from app.openapi import ADMIN_DOCS_URL, setup_openapi
from app.routes import datasets, files, jobs, sources, sync

setup_observability()
logger = logging.getLogger(__name__)

origins = ["http://localhost:3000", "https://www.alplakes.eawag.ch", "https://www.datalakes-eawag.ch", "https://www.datalakes.eawag.ch"]
allowed_prefix = "https://pr-"
allowed_suffix = ".dnujuz98d63cz.amplifyapp.com"

description = """
Datalakes API connects you to surface water datasets and is managed by the SURF department at [EAWAG](https://www.eawag.ch).

This API serves as the backend for the website [www.datalakes.eawag.ch](http://www.datalakes.eawag.ch).

### Disclaimer

The **Datalakes API** is provided "as is," without any guarantees regarding the accuracy, completeness, or timeliness of the data. While we strive to ensure data quality, users are responsible for verifying information before making any decisions based on it.

Additionally, we cannot guarantee continuous availability of the API. Service disruptions or maintenance periods may occur, and users should expect intermittent downtime.

### Get in Touch

For bug reports, collaboration requests, or to join our mailing list for updates, feel free to [get in touch](mailto:james.runnalls@eawag.ch).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
  logger.info("Application startup...")
  logger.info(f"Database: {get_safe_db_url(DATABASE_URL)}")
  logger.info("Testing database connection...")
  if not await check_db_connection(max_retries=3, retry_delay=2):
    logger.fatal("FATAL ERROR: Cannot connect to database!")
    await engine.dispose()
    sys.exit(1)
  logger.info("Database connection successful!")
  logger.info("Application ready.")

  yield

  logger.info("Shutting down application...")
  await engine.dispose()
  logger.info("Database connections closed")
  logger.info("Shutdown complete.")


app = FastAPI(
  title="Datalakes API",
  lifespan=lifespan,
  description=description,
  version="2.0.0",
  contact={
    "name": "James Runnalls",
    "email": "james.runnalls@eawag.ch",
  },
  license_info={
    "name": "Apache 2.0",
    "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
  },
)


class DynamicCORSMiddleware(CORSMiddleware):
  def is_allowed_origin(self, origin: str) -> bool:
    return origin in origins or (origin.startswith(allowed_prefix) and origin.endswith(allowed_suffix))


app.add_middleware(
  DynamicCORSMiddleware,
  allow_origins=origins,  # Still allow the fixed ones
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


add_exception_handlers(app)

app.include_router(datasets.router)
app.include_router(sources.router)
app.include_router(sync.router)
app.include_router(jobs.router)
app.include_router(files.router)

setup_openapi(app)  # after the routers, so both specs see every route


@app.get("/", include_in_schema=False)
def welcome():
  return {
    (
      "Welcome to the Datalakes API from Eawag. Navigate to /docs or /redoc for documentation, "
      f"or {ADMIN_DOCS_URL} for the full API including the endpoints for administrators. For queries please contact James Runnalls (james.runnalls@eawag.ch)."
    )
  }
