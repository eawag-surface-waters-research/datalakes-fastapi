"""
Logging and error reporting, shared by the API and the worker.
"""

import logging
import os

import sentry_sdk
from dotenv import load_dotenv

load_dotenv()

PRODUCTION = os.getenv("PRODUCTION", "false").lower() == "true"


def setup_observability() -> None:
  logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
  if PRODUCTION and not sentry_sdk.is_initialized():
    sentry_sdk.init(
      dsn="https://71eb5021a774490f9e1c273e06fc33de@o1106970.ingest.us.sentry.io/4509836725125120",
      traces_sample_rate=1.0,
    )
