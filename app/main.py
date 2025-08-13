from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware

import sentry_sdk

from app.routes import datasetparameters

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

app = FastAPI(
    title="Datalakes API",
    description=description,
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

app.include_router(datasetparameters.router)
