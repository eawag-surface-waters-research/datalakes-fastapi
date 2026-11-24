import argparse
import asyncio
import os

import uvicorn

from app.observability import setup_observability
from app.worker import run_worker


def main(argv: list[str] | None = None) -> None:
  parser = argparse.ArgumentParser(prog="python -m app.main", description="Starts the API or the job worker")
  parser.add_argument("--worker", action="store_true", help="Start the worker instead of the API.")
  worker_options = parser.add_argument_group("worker options")
  worker_options.add_argument(
    "--poll-interval",
    type=float,
    default=float(os.getenv("WORKER_POLL_INTERVAL", "2")),
    help="Seconds between checks for new jobs and cancel requests (env WORKER_POLL_INTERVAL).",
  )
  worker_options.add_argument(
    "--job-timeout",
    type=float,
    default=float(os.getenv("WORKER_JOB_TIMEOUT", "600")),
    help="Seconds a job may run before it's cancelled, 0 for no limit (env WORKER_JOB_TIMEOUT, default 10 minutes).",
  )
  args = parser.parse_args(argv)
  if args.worker:
    setup_observability()
    asyncio.run(run_worker(args.poll_interval, job_timeout=args.job_timeout or None))
  else:
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=True, server_header=False)


if __name__ == "__main__":
  main()
