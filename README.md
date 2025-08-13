# Datalakes FastAPI

[![License: MIT][mit-by-shield]][mit-by] ![Python][python-by-shield]

This is a repository for Datalakes fastapi.

Endpoints can be found in `app/main.py`

The documentation pages are available at the following:

- Swagger [http://localhost:8000/docs](http://localhost:8000/docs)
- Redoc [http://localhost:8000/redoc](http://localhost:8000/redoc)

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)

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

## Local Development

### 1. Install virtual environment

```console
conda create --name fastapi python=3.11
conda activate fastapi
conda install --file requirements.txt
```

### 2. Run API

Manually set filesystem path in main.py if files are NOT located in the `filesystem` folder within the repository.

```console
conda activate fastapi
docker compose -f docker-compose.db.yml up
uvicorn app.main:app --host 0.0.0.0 --reload
```

## Run Tests

Pytest can be directly run with FastAPI. Tests are located in the file `app/test_main.py`. 

### Install pytest

```console
pip install pytest
```

### Run tests

Navigate to the top directory of the repository to run pytest.

```console
conda activate fastapi
docker compose -f docker-compose.db.yml up
pytest
```

[mit-by]: https://opensource.org/licenses/MIT
[mit-by-shield]: https://img.shields.io/badge/License-MIT-g.svg
[python-by-shield]: https://img.shields.io/badge/Python-3.9-g