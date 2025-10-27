from typing import Annotated
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

import os
import sys
import sentry_sdk
from dotenv import load_dotenv

import app.auth as auth
from app.routes import selectiontables
from app.database import (
    check_db_connection,
    engine,
    DATABASE_URL,
    get_safe_db_url
)

load_dotenv()

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"

if PRODUCTION:
    sentry_sdk.init(
        dsn="https://71eb5021a774490f9e1c273e06fc33de@o1106970.ingest.us.sentry.io/4509836725125120",
        traces_sample_rate=1.0,
    )

origins = [
    "http://localhost:3000",
    "https://www.alplakes.eawag.ch",
    "https://www.datalakes-eawag.ch",
    "https://www.datalakes.eawag.ch"
]
allowed_prefix = "https://pr-"
allowed_suffix = ".dnujuz98d63cz.amplifyapp.com"

description = """
Datalakes API connects you to lakes datasets and is managed by the SURF department at [EAWAG](https://www.eawag.ch).

This API serves as the backend for the website [www.datalakes.eawag.ch](http://www.datalakes.eawag.ch).

### Disclaimer

The **Datalakes API** is provided "as is," without any guarantees regarding the accuracy, completeness, or timeliness of the data. While we strive to ensure data quality, users are responsible for verifying information before making any decisions based on it.

Additionally, we cannot guarantee continuous availability of the API. Service disruptions or maintenance periods may occur, and users should expect intermittent downtime.

### Get in Touch

For bug reports, collaboration requests, or to join our mailing list for updates, feel free to [get in touch](mailto:james.runnalls@eawag.ch).
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("\n" + "=" * 70)
    print("🚀 APPLICATION STARTUP")
    print("=" * 70)
    print(f"🔌 Database: {get_safe_db_url(DATABASE_URL)}")

    # Check database connection
    print("\n🔍 Testing database connection...")
    if not await check_db_connection(max_retries=3, retry_delay=2):
        print("\n" + "!" * 70)
        print("❌ FATAL ERROR: Cannot connect to database!")
        print("!" * 70)
        print("\n📋 Troubleshooting checklist:")
        print("  ☐ Is DATABASE_URL set correctly in .env?")
        print("  ☐ Is PostgreSQL running? (systemctl status postgresql)")
        print("  ☐ Can you connect manually? (psql -h host -U user -d dbname)")
        print("  ☐ Is the hostname resolvable? (ping hostname)")
        print("  ☐ Are credentials correct?")
        print("  ☐ Is the database firewall allowing connections?")
        print("!" * 70 + "\n")
        await engine.dispose()
        sys.exit(1)

    print("✅ Database connection successful!")
    print("\n" + "=" * 70)
    print("✅ APPLICATION READY")
    print("=" * 70 + "\n")

    yield

    # Shutdown
    print("\n" + "=" * 70)
    print("🔌 SHUTTING DOWN")
    print("=" * 70)
    await engine.dispose()
    print("✅ Database connections closed\n")

app = FastAPI(
    title="Datalakes API",
    lifespan=lifespan,
    description=description,
    swagger_ui_init_oauth={
        "clientId": GITHUB_CLIENT_ID,
        "appName": "GitHub OAuth API",
    },
    version="2.0.0",
    contact={
        "name": "James Runnalls",
        "email": "james.runnalls@eawag.ch",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    }
)

class DynamicCORSMiddleware(CORSMiddleware):
    def is_allowed_origin(self, origin: str) -> bool:
        return (
            origin in origins or
            (origin.startswith(allowed_prefix) and origin.endswith(allowed_suffix))
        )

app.add_middleware(
    DynamicCORSMiddleware,
    allow_origins=origins,  # Still allow the fixed ones
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    print(exc)
    return JSONResponse(
        status_code=500,
        content={"message": "Server processing error - please check your inputs. The developer has been notified, for "
                            "updates on bug fixes please contact James Runnalls (james.runnall@eawag.ch)"})


@app.get("/", include_in_schema=False)
def welcome():
    return {"Welcome to the Datalakes API from Eawag. Navigate to /docs or /redoc for documentation. For "
            "queries please contact James Runnalls (james.runnall@eawag.ch)."}

@app.post("/api/auth/token", tags=["Authentication"], summary="Swagger UI OAuth2 Token Exchange", include_in_schema=False)
async def github_token(
    code: Annotated[str, Form(..., description="The authorization code from GitHub.")]
):
    """
    Handles the token exchange from the authorization code.
    This endpoint is designed specifically to be called by the Swagger UI.
    It returns a token in the format expected by Swagger UI's authorization flow.
    """
    token = await auth.get_access_token(code)
    return {"access_token": token, "token_type": "bearer"}

app.include_router(selectiontables.router)
