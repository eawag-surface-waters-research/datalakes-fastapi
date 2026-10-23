# Datalakes API v3: Development Roadmap

This is the working plan for the next version of the Datalakes API. It sets out **what** needs to be built, in roughly the order it should be built, and **suggests** how to do each part.

It is a starting point, not a contract. You own it from now on:

- If you find a better approach, lets discuss it, then and update this document so it stays true.
- If a step turns out to be in the wrong order, too big or unnecessary, we can change the plan.

---

## Contents

- [Background](#background)
- [Where things stand today](#where-things-stand-today)
- [Guiding principles](#guiding-principles)
- [The steps](#the-steps)
  - [Step 0: Get set up](#step-0-get-set-up)
  - [Step 1: Generalise the database](#step-1-generalise-the-database)
  - [Step 2: A better way to connect to datasets](#step-2-a-better-way-to-connect-to-datasets)
  - [Step 3: A separate worker for downloading and processing](#step-3-a-separate-worker-for-downloading-and-processing)
  - [Step 4: Convert NetCDF and HDF files to Zarr](#step-4-convert-netcdf-and-hdf-files-to-zarr)
  - [Step 5: Data availability](#step-5-data-availability)
  - [Step 6: Endpoints for serving data](#step-6-endpoints-for-serving-data)
  - [Step 7: An nginx caching layer](#step-7-an-nginx-caching-layer)
  - [Step 8: Downloading large subsets of data](#step-8-downloading-large-subsets-of-data)
  - [Step 9: Plan the migration from the existing database](#step-9-plan-the-migration-from-the-existing-database)
- [Throughout: things that apply to every step](#throughout-things-that-apply-to-every-step)
- [Useful references](#useful-references)

---

## Background

[Datalakes](https://www.datalakes.eawag.ch) publishes environmental datasets (mostly lake monitoring data) from Eawag's SURF department. The current backend is a [Node.js API](https://github.com/eawag-surface-waters-research/datalakes-nodejs). It converts every NetCDF file into a JSON file on disk, and the website fetches those files one at a time and stitches them together in the browser. This is slow, it cannot answer simple questions like "give me temperature at 0–10 m for March", and the data model is tied to lakes. The API is also a nightmare for people to try and access and connect to 
so this needs to be improved.

This repository (`fastapi/`) is the replacement. The goal is an API that:

- is **not specific to lakes**, so it can hold rivers, stations or other kinds of environmental data later;
- pulls data from wherever it lives (S3 first, later other openly accessible storage such as a public URL) and **processes it in the background**;
- stores data in **Zarr**, so any time range, variable or depth range can be read quickly;
- has **fast, cached, intuitive endpoints** that are not tied to the current website (the website will be rebuilt against the new API);
- lets users **download large amounts of data** without breaking the server;
- runs on a **small server**.

## Where things stand today

What already exists in this repo:

| Area | State |
|---|---|
| CRUD endpoints | Datasets, dataset parameters, repositories, selection tables (vocabularies), maintenance. Async SQLModel, see [app/routes/](../app/routes/). |
| Auth | GitHub OAuth. Reads are public; writes need membership of the Datalakes GitHub team. See [app/auth.py](../app/auth.py). |
| Database | Postgres, created from a `pg_dump` in [db/](../db/). There are no migrations, and few foreign keys or indexes. |
| Ingestion | Started in [app/routes/remote.py](../app/routes/remote.py): add an rclone remote and list its files, or download and unzip a link. |
| Repositories | Cloning git repositories runs as a FastAPI background task in [app/routes/repositories.py](../app/routes/repositories.py). |
| Tests | pytest against the dev database, in [app/tests/](../app/tests/). |
| Test data | Example datasets (one for each plot type, plus others) are available on request. Ask for them when you need them for tests. |


## Guiding principles

These are the ideas the rest of the plan is built on. If you want to break one, that's fine, but make it a deliberate decision and update this document.

1. **Domain-neutral.** Nothing in the core model should assume "lake". A lake is just one type of site.
2. **The API process only reads.** Downloading, parsing, converting and building big exports happen in a worker, never in an HTTP request.
3. **Processed data is immutable and versioned.** An update writes new data and then switches over to it. Every dataset has a `version` that goes up when its data changes. This makes caching and cache invalidation simple.
4. **Keep the original files.** The source files are the archive. Everything else (Zarr, availability, caches) can be rebuilt from them.
5. **Small server.** Prefer simple infrastructure and few moving parts. Every new service in `docker-compose.yml` should earn its place.

---

## The steps

Each step lists a **goal**, a **suggested approach**, **open questions** for you to answer, and what **done** looks like. The approaches are suggestions. The goals and "done when" are what matter.

**Every step expands the pytest suite to cover all the functionality it adds.** This is part of "done when" for every step, even where it isn't written out, and the whole suite should pass before a step is considered finished. If you need example datasets to test against, ask for them.

Steps 2 and 3 overlap a lot: connecting to a source is only useful once something can run the download. It's fine to work on them together. Similarly, it's a good idea to get a thin version of Steps 4–6 working for **one simple time-series dataset** before handling every format and feature type.

### Step 0: Get set up

**Goal:** You can run everything locally, and the foundations are safe to build on.

**Suggested approach:**

- Run the API and database locally by following the [README](../README.md). Log in through Swagger with GitHub and try the existing endpoints.
- Read through `app/` and `db/datalakes_schema.sql`. Look at the old Node.js API and the current website to see how data is used today.

**Done when:** you can run the stack and the tests locally and understand the function and operation of the existing system

### Step 1: Generalise the database

**Goal:** A data model that isn't specific to lakes and can describe any gridded or time-series environmental dataset. The existing endpoints work against it.

**Suggested approach:**

- **Sites, not lakes.** Replace `lakes` with a general `sites` table (a feature of interest): name, `type` (lake, river, station…), geometry, plus a JSON column for type-specific properties like elevation or max depth.
- **Variables, not axes.** `datasetparameters` describes each variable by a plot axis (`x`, `y`, `z`, `M`). Describe variables by what they *are* instead: name, name in the source file, role (time, coordinate, data), dimensions, unit, and a link to the `parameters` vocabulary. See [Why move away from `axis`](#why-move-away-from-axis) below.
- **Describe the shape of the data.** The [CF conventions' discrete sampling geometries](https://cfconventions.org/Data/cf-conventions/cf-conventions-1.11/cf-conventions.html#discrete-sampling-geometries) are a good vocabulary for this: `timeSeries` (a line graph), `timeSeriesProfile` (a heatmap over time and depth) and `profile` (individual casts). Storing the feature type per dataset lets the API and the website decide how to query and draw it.
- **Remove legacy columns** that no longer make sense, e.g. `mapplotfunction`, `renku`, `prefile`, `prescript`, `fileconnect`, `liveconnect`, `embargo`/`password` (we want to remove the embargo functionality).
- **Rename `maintenance` to `issues`.** An issue marks a time period on a dataset, or on one of its variables, where something was wrong with the data (maintenance, sensor fault, calibration…). It can be limited to a depth range, and it has a type, description, reporter and state. Rename the existing `issue` column (e.g. to `type`) so you don't end up with `issues.issue`. Leave events such as storms or ice cover out of this table (see the open question in Step 2).
- **Make the git repository an optional link.** Today every dataset needs a git repository, because that's how the old API finds its data. In the new model, the data location is set on its own (Step 2), and `repositories_id` becomes optional. Keep it as a link to the code that processes the data, for provenance.
- Add real **foreign keys and indexes**.
- **Dataset IDs must be preserved.** Keep `datasets.id` as the dataset's identifier, so every production dataset keeps its current ID after the migration (Step 9). Don't introduce a new primary key that replaces it.
- Update the models, routes and tests to match. Keep the existing SQLModel Base/Create/Update pattern.

#### Why move away from `axis`

Today the `axis` column on `datasetparameters` is the only thing that says what a variable is, so it ends up doing several unrelated jobs:

| Job | How `axis` does it today |
|---|---|
| **Role** | `x` and `y` are coordinates or 1-D series. `z` is a 2-D data array. Numbered suffixes (`y1`, `y2`) add extra series. |
| **Shape** | To read a `z` variable, the converter finds the `x` and `y` variables and matches them to the array's dimension names by hand. |
| **Plot layout** | Whether time is `x` or `y` decides whether a heatmap has time along the bottom or up the side. |
| **Dataset type** | `M` on the time variable means the dataset is a set of separate profiles (casts), each with a single time, not a continuous series. The converter then keeps each file separate instead of joining them, and the website switches to profile mode. |
| **Where to find the data** | The converted JSON files are keyed by axis, so to find time you look up which axis time is on, then read that key. |

Mixing these jobs causes problems for the new system:

- **A display choice is stored as if it were part of the data.** Time on x or time on y is how one website chose to draw a dataset. The new website, Alplakes, or a scientist using Python may want to look at the same data differently.
- **The new endpoints need names, not letters.** A request like `?variables=temperature&depth_min=0&depth_max=10` needs the API to know which variable is depth and which dimension it runs along. With `axis`, depth could be `x` in one dataset and `y` in another. Responses like `{"x": …, "y": …, "z": …}` mean nothing to someone who doesn't also have the dataset config.
- **It doesn't stretch to other kinds of data.** Spectra with a wavelength dimension, or gridded data with time, depth, latitude and longitude, don't fit into x, y and z.
- **Dataset-level facts are hidden in one variable.** Whether a dataset is a profile or a time series is a property of the whole dataset, but today you can only find it by checking whether any variable has `M`.
- **It repeats what the files already say.** NetCDF, xarray and Zarr all use named dimensions. Recording each variable's dimensions directly means nothing has to be inferred, and nothing can disagree with the file.

A suggested split:

- **On each variable:** what it is (role, dimensions, unit, parameter).
- **On the dataset:** what kind of data it is (the feature type from "Describe the shape of the data" above: time series, time series of profiles, or separate profiles).
- **Separately, as plot configuration:** preferences such as "heatmap with time on x". The website can use these, but the API doesn't need to understand them.

Converting the existing datasets is mostly mechanical:

| Today | New model |
|---|---|
| `x` / `y` | A coordinate (e.g. time, depth) or a 1-D data variable |
| `z` | A data variable whose dimensions are the `x` and `y` variables |
| `M` on time | Dataset feature type is `profile`; time becomes a coordinate with one value per cast |
| Time on `x` vs time on `y` | A plot configuration setting, not part of the data model |

The tables that later steps need (sources, files, jobs, availability) can be added in those steps. You don't need to design everything up front.

**Open questions:**

- How much of the plotting configuration (`plotproperties`) belongs in the API? One option is to store it as opaque JSON the API doesn't interpret.
- Are the CF feature types enough for the datasets we have? Check them against the example datasets.

**Done when:** the new schema is in Alembic migrations, nothing in it assumes "lake", and the existing CRUD endpoints and tests work against it.

### Step 2: A better way to connect to datasets

**Goal:** A dataset owner can point Datalakes at where their data lives, test the connection, and choose which files to include.

> **Requirement: the data must be openly accessible. Datalakes will not store anyone's credentials.** A source can only be added if its files can be read anonymously, e.g. a public S3 bucket or a public URL. No access keys, passwords or tokens are ever accepted or stored. If a dataset's data isn't public, the owner has to make it public (or copy it to a public location) before it can be added.

**S3 is the priority.** All current datasets keep their data in S3, so solid S3 support comes first, and other backends are added only after it works well. Today the old API finds a dataset's bucket through a `.bucket` file in the dataset's git repository, then runs `aws s3 sync` on a path built from the repository name (see [routes/update.js](https://github.com/eawag-surface-waters-research/datalakes-nodejs/blob/HEAD/routes/update.js) in datalakes-nodejs). In the new system, the data location (e.g. S3 bucket and prefix) is given directly when a dataset is added, **independently of any git repository**. Every existing dataset's location must be expressible that way, because the migration (Step 9) depends on it.

**Suggested approach:**

- **Use rclone.** It handles S3 well, and it also supports almost every other storage backend (S3, HTTP, public WebDAV shares…) through one interface, so we don't have to write a connector for each one. Only backends that can be read anonymously are relevant: for S3, that means configuring rclone with no access keys.
- **Look at Renku 2.0's data connectors** for a working example of exactly this. Renku builds its "add data connector" form from rclone's own provider schema (`rclone config providers` outputs JSON describing every backend and its options), so new backends work without new code. Unlike Renku, we only need the public, no-credentials part of this.
- **Don't use a shared `rclone.conf`.** Build each source's config when it's needed, e.g. as an on-the-fly connection string (`:s3,provider=AWS:bucket/path`).
- Store each source in a `sources` table: type (e.g. rclone, url), config, path or prefix, include patterns. There are no credential fields. **Reject any source config that contains credentials** (access keys, passwords, tokens), and test the connection anonymously before saving it.
- **Git repositories are not a source.** A dataset can optionally link to the repository that holds its processing code, but the data location is always given separately, and nothing has to be cloned to find the data.
- Later, **plain URLs** can be a source type too. For URLs, block private and internal IP addresses, limit the download size, and extract archives safely (reject absolute paths and `..`, cap file count and total size).
- Provide endpoints to **test a connection and list files**, so the website can show a file browser and let the user pick a folder or pattern.

**Open questions:**

- After S3, which backends do users actually need? Ask around before building more.
- Some existing repositories hold more than the `.bucket` file, such as events files or notes. Where should those live now that the repository is optional: next to the data in S3, in the database, or still in git?
- Should listing files happen in the API (fast feedback for the user) or in the worker (safer, because no network calls from the API)? A short-lived job with a timeout might be a good compromise.

**Done when:** a member can add an S3 source (bucket and prefix) to their dataset and list its files without any credentials, the S3 location of every existing dataset can be represented as a source, and a source config containing credentials is rejected (with a test for it). Other backends and URL sources are a bonus at this stage. The security problems in `remote.py` are gone.

### Step 3: A separate worker for downloading and processing

**Goal:** Long-running work (syncing files, converting, building exports, cloning repos) runs in its own process, not in the API.

**Suggested approach:**

- **Same Docker image, different command.** Add a `worker` service to `docker-compose.yml`. The API mounts the data volume read-only; the worker mounts it read-write.
- **Pick a job queue.** Some options:
  - A **Postgres-backed queue** (e.g. [procrastinate](https://procrastinate.readthedocs.io), or a hand-rolled `jobs` table with `SELECT … FOR UPDATE SKIP LOCKED`). No new infrastructure.
  - **arq** or **Celery** with Redis. More features, but one more service to run.

  Since caching is going into nginx (Step 7), we may not need Redis at all. That argues for a Postgres queue, but it's your call.
- **A `jobs` table** that records status, progress, errors and logs, so users can see what happened to their sync. Expose it through `GET /jobs/{id}` and `GET /datasets/{id}/jobs`.
- **One job per dataset at a time** (a lock per dataset), so two syncs can't overwrite each other.
- **Triggers:** an API call (`POST /datasets/{id}/sync`) and a schedule for live datasets. Today's processing pipelines call an update URL after uploading new data, so they will need a way to authenticate, such as a per-dataset token.
- If repositories still need to be cloned for anything (see the open question in Step 2 about events files), do it in the worker rather than in a FastAPI background task as `repositories.py` does today.
- Report worker errors to Sentry.

**Open questions:**

- How are scheduled (live) syncs defined: a per-dataset interval, or cron expressions?
- How much of a job's log should be kept, and for how long?

**Done when:** triggering a sync creates a job, the worker downloads the files from the source, and the job's status and errors are visible through the API. Restarting either container doesn't lose jobs.

### Step 4: Convert NetCDF and HDF files to Zarr

**Goal:** The worker turns a dataset's source files into Zarr, which the API can read efficiently, using only the variables defined in Step 1.

**Suggested approach:**

- **Input formats.** All input data must be NetCDF, HDF5 or an equivalent self-describing format, i.e. one that stores named variables, dimensions and attributes. CSV and other plain-text formats are not accepted. Files in an unsupported format should be rejected with a clear error.
- **One reader per format.** A small plugin interface that turns a file into a normalised `xarray.Dataset` (renamed variables, decoded time, the declared dimensions). Start with NetCDF. Keep the interface general, so that supporting another equivalent format later means adding one reader, not changing the pipeline.
- **An "inspect" endpoint** that reads one sample file and suggests a variable mapping. This makes setting up a new dataset much easier.
- **Validate each file** before converting it. One bad file should be marked as failed with a clear error, not stop the whole dataset.
- **Keep a file index** (path, size, modification time, hash, time range, status), so a sync only processes files that are new or have changed.
- **Think about the Zarr layout early.** Things to decide:
  - One store per dataset, or **partitions** (e.g. one per year, or per month for high-frequency data)? Partitions mean an update only rebuilds the latest period, and you can write new partitions and then switch to them atomically, which ties in nicely with versioning and caching.
  - What happens when the depth axis changes between files? One option is a separate "segment" each time it changes.
  - Chunk sizes, compression (e.g. zstd), Zarr v3 sharding to keep the file count down.
  - How missing values and quality flags are stored.
- **After each successful build:** update the dataset's time and depth extents (recomputed, so they can shrink as well as grow) and bump `datasets.version`.
**Open questions:**

- Is virtual Zarr / [VirtualiZarr](https://virtualizarr.readthedocs.io) (referencing the original NetCDF files rather than copying the data) worth considering for some datasets? `todo.md` mentions it.
- What chunk sizes give good read performance for the common queries (a week of recent data, a year at low resolution)?

**Done when:** each of the example datasets converts to Zarr, and values read back from Zarr match values read directly from the source files (an automated "golden" test for random time windows). Adding a new file to a source and syncing updates the Zarr and bumps the version.

### Step 5: Data availability

**Goal:** Quickly answer "for which time periods does this dataset have data?" for each variable, without reading the data itself.

**Suggested approach:**

- **Compute it during processing (Step 4)**, not at request time. For each variable, find the runs of valid (non-missing, not flagged) values and store them as intervals in a table (e.g. `coverage`: dataset, variable, start, end, number of valid values).
- **Merge small gaps** so you don't store thousands of tiny intervals. A gap smaller than a few times the normal sampling interval can be treated as continuous.
- **Recompute it for the parts that changed** on each update, so availability can shrink when data is removed.
- An endpoint such as `GET /datasets/{id}/availability?start=&end=&variables=`, which returns either the raw intervals or the fraction of valid data per day, month or year (useful for long timelines on the website).
- Possibly a cross-dataset version (`GET /availability?…`) to answer "which datasets have data for this period and area?". This could also replace the current `monitor` freshness check.

**Open questions:**

- What gap threshold makes sense? It probably needs to be configurable per dataset.
- Should issues from Step 1 (e.g. "sensor broken") count as gaps?

**Done when:** availability is calculated as part of processing, the endpoint answers in a few milliseconds, and the result matches the data in the test datasets.

### Step 6: Endpoints for serving data

**Goal:** Clear, predictable endpoints for reading data from the Zarr stores. They should make sense to someone using the API directly (scientists, scripts, other services), not just to the website.

**Suggested approach:**

- **Look at the [Alplakes API](https://alplakes-api.eawag.ch/docs)** for style. It has readable, resource-oriented URLs, consistent parameter naming and time formats, and good Swagger documentation with examples. Aim for the same feel, so the two Eawag APIs are familiar to the same users.
- **Don't design around the current website.** It will be rebuilt against this API. Design for the questions people ask of the data; for example:
  - Which datasets exist? (search by site, parameter, time, area)
  - What's in this dataset? (metadata, variables, extents, version)
  - What's the latest value?
  - Give me these variables, for this time range, at these depths.
  - Give me a version of this that's light enough to plot (server-side resampling or decimation, e.g. a `max_points` or `resample=1h` parameter).
- **Self-describing responses.** Return units, dimensions and coordinates alongside the values, rather than `x`/`y`/`z` blobs that only make sense with the dataset config.
- **Offer a few formats** for the same query: JSON by default, with CSV and NetCDF as options.
- **Cap the size of synchronous responses.** If a request is too big, return a clear error pointing to the large-download route (Step 8).
- **Include the dataset `version` in responses**, so clients (and the cache in Step 7) can tell when the data has changed.
- **Read Zarr off the event loop.** Zarr and xarray reads block, so run them in a thread pool with a limit on how many run at once, to protect a small server.

**Open questions:**

- Path parameters (Alplakes style, `/data/{dataset}/{start}/{end}`) or query parameters (`/datasets/{id}/data?start=&end=`)? Alplakes uses path parameters; query parameters cope better with many optional filters. Pick one and use it everywhere.
- How should profile datasets (casts at irregular times) be queried compared with time series?
- How should quality flags and issues be applied: masked by default, or returned alongside the data?

**Done when:** you can fetch any time window, set of variables and depth range for each test dataset, the responses are documented in Swagger with examples, and a typical plot query (one year, one variable) is fast. Benchmark it against the current Node.js API.

### Step 7: An nginx caching layer

**Goal:** Repeated requests are served from an nginx cache in front of the API, so the API only does work when it has to.

How the cache works is up to you. It must meet these requirements:

- **nginx** runs as a reverse proxy in front of the API, as part of `docker-compose.yml`.
- Repeated identical requests to public read endpoints (data, availability, metadata) are served from the cache without reaching the API.
- **A dataset update must invalidate that dataset's cached responses.** Once a sync has changed a dataset, no request may get the old data back from the cache. This must happen automatically as part of the update, with no manual step. Other datasets' cached responses are not affected.
- Authenticated requests and write requests are never cached.
- The cache has a bounded size on disk.
- Maintainers have a way to clear a dataset's cache by hand, as a backstop.

**Done when:** a repeated request is served by nginx without reaching the API, and an automated test shows that after a sync updates a dataset, the next request returns the new data.

### Step 8: Downloading large subsets of data

**Goal:** Users can download large amounts of data (years of data, many variables, whole datasets) easily, without timeouts and without overloading the server.

**Suggested approach:** There are several routes, and different users will want different ones:

- **Streaming.** For medium-sized requests, stream CSV or Parquet from the data endpoint one chunk or partition at a time, so memory use stays flat.
- **Asynchronous exports.** For big requests: `POST /exports` creates a job; the worker writes the file (NetCDF, CSV, Parquet or a zip); the user polls for status and then downloads it. Name exports after a hash of dataset + version + query, so a repeat request returns the existing file. Delete old exports after a while.
- **The original files.** List a dataset's source files by time range and download them one at a time, or as a zip streamed on the fly.
- **Direct Zarr access.** For power users, publish the Zarr stores as static files (via nginx) and document how to open them with `xarray` in a few lines of Python. They can then slice whatever they want with no load on the API.

Think about the experience in the website as well as the API: show an estimated size before downloading, tell the user when their export is ready, and make it clear which route is right for which use.

**Open questions:**

- Which of these do users actually want? Talk to a few of them before building all four.
- Should export downloads need a login, to protect against abuse?
- How much disk can exports use?

**Done when:** a user can get a multi-year, multi-variable extract of any test dataset in a format of their choice, without the API's memory use spiking, and the routes are documented with examples.

### Step 9: Plan the migration from the existing database

**Goal:** A tested, repeatable plan for moving every production dataset from the current database onto the new system, so the Node.js API can be switched off.

> **Requirement: every dataset must keep the same ID after the migration.** Existing links, scripts and processing pipelines refer to datasets by ID, so this is not negotiable. The migration script must insert datasets with their existing IDs, never let the database assign new ones. Afterwards, move the ID sequence past the highest migrated ID (`setval`) so that new datasets don't collide with migrated ones.

**Suggested approach:**

- **Map the old schema to the new one** (datasets, dataset parameters → variables, lakes → sites, maintenance → issues). Each dataset's `.bucket` file and repository path become an S3 source (bucket and prefix), and its repository is kept only as the optional code link. Write it as a script that can be run again from scratch, not as a series of manual steps.
- **Only migrate metadata, not derived data.** The old `files` table and JSON files don't need to come across; everything derived (Zarr, file index, availability, extents) is rebuilt from the source files.
- **Add a "regenerate all datasets" endpoint** (maintainers only). It queues a full sync and rebuild for every dataset, or for a filtered list, and reports overall progress and which datasets failed. The migration then becomes two steps: run the migration script, then immediately call regenerate-all to rebuild everything from source. The endpoint stays useful afterwards too: after a bug fix in processing, a change to the Zarr layout, or to recover from data loss.
  - It should go through the worker's normal queue, so it doesn't overload the server or block other syncs for long.
  - It should be safe to stop and resume, and to re-run for only the datasets that failed.
- **Rehearse on a copy of production.** Measure how long a full regeneration takes and fix the datasets that fail, before doing it for real.
- **Check every existing bucket is publicly readable.** The old API reads S3 with the server's own AWS credentials, so some buckets may not be public. List any that aren't, and make them public (or move their data) before migrating, because the new system won't hold credentials (Step 2).
- **Embargoed or password-protected datasets need a deliberate decision.** Removing embargo would make them public, so don't migrate them automatically.
- **Check the results.** Confirm that every dataset ID in the old database exists in the new one, with the same title. Compare values from the new API against the old one for sample time windows in each dataset.
- Write the plan up as a short runbook: the order of steps, expected duration and downtime, how to check it worked, and how to roll back.

**Open questions:**

- Can the old and new systems run side by side until the new website is ready, or is there a hard switch-over?
- How long can datasets be unavailable while they regenerate?
- What happens to datasets whose original source is no longer reachable? Do we need to keep a copy of the files the old system holds?

**Done when:** the migration script and the regenerate-all endpoint exist and are tested, a test confirms that every migrated dataset keeps its original ID, and the whole migration has been rehearsed end to end on a copy of production, with a runbook for the real switch-over.

---

## Throughout: things that apply to every step

- **Tests.** Expand the pytest suite at every step so it covers all the functionality added: endpoints (including permission checks and error cases), worker jobs, file conversion, availability and caching. The most valuable tests are end to end with real example files: connect → sync → convert → query → compare to the source. Example datasets can be provided on request. Ask when you need them, and say what kind of data you're trying to cover.
- **Small pull requests.** Aim for PRs that can be reviewed in under half an hour. A thin end-to-end slice beats a perfect layer that nothing uses yet.
- **Demo as you go.** Show working endpoints in Swagger at the end of each step; it's the fastest way to get feedback.
- **Document the API in the API.** Good Swagger descriptions and examples are the user documentation.
- **Clean up after deletes.** When a dataset is deleted, its sources, Zarr stores, caches and exports should go too (`clean.js` in `todo.md`).
- **Keep this document up to date.**

## Useful references

- [todo.md](../todo.md): which old Node.js routes are being ported or dropped
- [datalakes-nodejs](https://github.com/eawag-surface-waters-research/datalakes-nodejs): the current API that this project replaces
- [datalakes-react](https://github.com/eawag-surface-waters-research/datalakes-react): the current website
- Example datasets for testing: available on request
- [Alplakes API docs](https://alplakes-api.eawag.ch/docs) and [alplakes-fastapi](https://github.com/eawag-surface-waters-research/alplakes-fastapi) (its source code): style reference for Step 6, and an example of a FastAPI service built by the same team
- [renku-data-services](https://github.com/SwissDataScienceCenter/renku-data-services): Renku 2.0's backend, including its rclone-based data connectors. An example for Step 2.
- [rclone docs](https://rclone.org/docs/), especially [environment variables](https://rclone.org/docs/#environment-variables) and [connection strings](https://rclone.org/docs/#connection-strings)
- [Zarr](https://zarr.readthedocs.io) and [xarray Zarr I/O](https://docs.xarray.dev/en/stable/user-guide/io.html#zarr)
- [CF conventions: discrete sampling geometries](https://cfconventions.org/Data/cf-conventions/cf-conventions-1.11/cf-conventions.html#discrete-sampling-geometries)
- [nginx proxy_cache](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_cache)
