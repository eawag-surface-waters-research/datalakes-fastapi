# Datalakes FastAPI

[![License: MIT][mit-by-shield]][mit-by] ![Python][python-by-shield]

> [!NOTE]
> This repository is still under active development. Endpoints, data models and documentation may change without notice.

This is the repository for the new [datalakes](https://www.datalakes-eawag.ch/) API, which is built with [FastAPI](https://fastapi.tiangolo.com/).
This API is designed to be a replacement for the existing [NodeJS-based datalakes API](https://github.com/eawag-surface-waters-research/datalakes-nodejs).

Endpoints can be found in `app/main.py`

The documentation pages are available at the following:

- Swagger [http://localhost:8000/docs](http://localhost:8000/docs)
- Redoc [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Production Deployment

### 1. Install docker

Follow the official install instructions [here](https://docs.docker.com/engine/install/)

Then run `docker login` to enable access to private docker images.

### 2. Clone repository
```console
sudo apt-get -y update && sudo apt-get -y install git
git clone https://github.com/eawag-surface-waters-research/datalakes-fastapi.git
mkdir -p filesystem
```

### 3. Create .env file
Replace example filesystem path with correct path. For local development this is likely to be the `filesystem` folder
within the repository. For production environments this may be on another drive.
```console
cd datalakes-fastapi
cp env.example .env
nano .env
```

Environment variables that control the API:

| Variable                         | Default                 | What it controls                                                                                                               |
| -------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `DB_USER`                        | none                    | User for the Postgres database.                                                                                                |
| `DB_PASSWORD`                    | none                    | Password for the Postgres database.                                                                                            |
| `DB_DATABASE`                    | none                    | Name of the Postgres database.                                                                                                 |
| `DB_HOST`                        | `localhost`             | Host of the Postgres database.                                                                                                 |
| `DB_PORT`                        | `5432`                  | Port of the Postgres database.                                                                                                 |
| `PRODUCTION`                     | `false`                 | `true` sends errors to Sentry and pools database connections. Otherwise every request opens its own connection.                |
| `FILESYSTEM`                     | none                    | Folder on the host that Docker Compose mounts as `/code/filesystem` in the container.                                          |
| `SOURCE_ALLOW_PRIVATE_ENDPOINTS` | `false`                 | `true` accepts S3 endpoints over plain HTTP and in private networks, e.g. the local test S3 server. Never allow in production. |
| `TEST_S3_ENDPOINT`               | `http://localhost:8333` | Only for the tests: where the local S3 server from `docker-compose.test.yml` runs.                                             |

### 4. Launch service
```console
cd datalakes-fastapi
docker compose up --build -d
```

### Restart service
When there are changes to the API the service needs to be rebuilt to reflect those changes.

```console
docker compose down
docker compose up --build -d
```

## Authentication

Authentication uses OAuth2 with the GitHub API. GET requests are public however; POST, PUT & DELETE requests require
login with a GitHub account. This can be done using the Swagger documentation or via the Datalakes website.

Permissions are controlled using the Datalakes GitHub team: https://github.com/orgs/eawag-surface-waters-research/teams/datalakes

Maintainers of the Datalakes team have full admin rights whereas members are only able to add and edit only their
own datasets (limited rights).

## Local Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/). They are declared in `pyproject.toml` and locked in
`uv.lock`.

### 1. Install uv

Follow the official install instructions [here](https://docs.astral.sh/uv/getting-started/installation/)

### 2. Install dependencies

uv creates a virtual environment in `.venv` with Python 3.14 (automatically downloaded if needed) and installs the locked
dependencies, including the dev dependencies such as pytest.

```console
uv sync
```

### 3. Run API and Worker

Manually set filesystem path in main.py if files are NOT located in the `filesystem` folder within the repository.

```console
docker compose -f docker-compose.db.yml up -d
uv run alembic upgrade head
uv run python -m app.main # starts the API on http://localhost:8000
```

The database schema is created by migrations. Run `uv run alembic upgrade head` after starting a fresh database and after pulling new migrations. With `docker-compose.yml`, the `migrate` service does this before the API starts.

Jobs (fetches, syncs, rebuilds, exports) are run by a separate worker process. Run it next to the API:

```console
uv run python -m app.main --worker                       # runs queued jobs until Ctrl+C
uv run python -m scripts.queue_test_job --seconds 30     # queues a test job, to check that the worker runs, then follow it at /jobs/{job_id}
```

With `docker-compose.yml`, the `worker` service runs it.

### 4. Contribute

Before contributing, If you want to contribute to the codebase, please install the pre-commit hooks by running:

```console
pre-commit install
```

This will execute a set of checks before each commit, helping to maintain code quality and consistency in the project.

### Manage dependencies

Add or remove packages with uv rather than pip, so `pyproject.toml` and `uv.lock` stay in sync. Commit both files.

```console
uv add <package>
uv add --dev <package>
uv remove <package>
uv lock --upgrade
```

## Database migrations

The database schema in [app/models/](app/models/) is managed with Alembic. Migrations live in `migrations/versions`.

After changing a model:

```console
uv run alembic revision --autogenerate -m "add xyz"   # generate a draft, then review it
uv run alembic upgrade head                           # apply it
uv run alembic downgrade -1                           # undo the last migration
uv run alembic check                                  # models and database differ?
```

Always review the generated file. Autogenerate does not detect renames (it drops and adds), changes of allowed enum values (they are CHECK constraints), or data backfills. Commit the model change and the migration together. there is a test in place that fails if the migrations and the models disagree.

## Run Tests

Pytest can be directly run with FastAPI. Tests are located in the folder `app/tests`.

Navigate to the top directory of the repository to run pytest. You need to make sure the database container is running, and the local S3 server for the source tests. The source tests also need [rclone](https://rclone.org/install/) installed.

```console
docker compose -f docker-compose.db.yml up -d
docker compose -f docker-compose.test.yml up -d
uv run pytest
```

Without the S3 server, the tests that list a source are skipped.

[mit-by]: https://opensource.org/licenses/MIT
[mit-by-shield]: https://img.shields.io/badge/License-MIT-g.svg
[python-by-shield]: https://img.shields.io/badge/Python-3.14-g
