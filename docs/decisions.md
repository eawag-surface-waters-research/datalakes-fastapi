# Decisions

This file records decisions made while working through the [roadmap](roadmap.md): the question, the answer, and what it changes in the plan. The roadmap stays as written. Where a decision here changes the plan, this file takes precedence.

Each entry has a status:

- Decided: agreed, build it this way.
- Proposed: a suggestion that follows from a decision but still needs a yes.

## Contents

| #   | Decision                                                                                                 | Status  |
| --- | -------------------------------------------------------------------------------------------------------- | ------- |
| 1   | [The Airflow pipelines move to the new API](#1-the-airflow-pipelines-move-to-the-new-api)                | Decided |
| 2   | [uv instead of conda](#2-uv-instead-of-conda)                                                            | Decided |
| 3   | [Redis instead of nginx as the cache](#3-redis-instead-of-nginx-as-the-cache)                            | Decided |
| 4   | [The serverless fallback is out of scope](#4-the-serverless-fallback-is-out-of-scope)                    | Decided |
| 5   | [Sources: S3 and SWITCHdrive](#5-sources-s3-and-switchdrive)                                             | Decided |
| 6   | [Hidden datasets are migrated](#6-hidden-datasets-are-migrated)                                          | Decided |
| 7   | [Datasets 1 and 16 are not migrated; link datasets](#7-datasets-1-and-16-are-not-migrated-link-datasets) | Decided |
| 8   | [Embargoed datasets are migrated without embargo](#8-embargoed-datasets-are-migrated-without-embargo)    | Decided |
| 9   | [Leave out authentication for now?](#9-leave-out-authentication-for-now)                                 | Proposed    |

---

## 1. The Airflow pipelines move to the new API

Decided on 2026-10-28.

**Question:** Is it fine if the `/update/:id` and `/refresh/:id` endpoints are removed after the migration? Those endpoints are regularly called by the Airflow DAG to update datasets, and the new API has a different way to trigger updates.

**Answer:** Yes, the pipelines can be updated to call the new sync trigger instead. The old endpoints are not needed anymore.

**What this means:**

- The old update endpoints don't need to be kept. The pipelines are updated at switch-over to call the new sync trigger from [Step 3](roadmap.md#step-3-a-separate-worker-for-downloading-and-processing).
- The DAG currently runs 25 pipelines that update datasets. Some update every 20 or 30 minutes, most hourly, the rest every 6 hours or daily. [Step 4](roadmap.md#step-4-convert-netcdf-and-hdf-files-to-zarr) must therefore handle frequent small appends cheaply.

## 2. uv instead of conda

Decided on 2026-10-28.

**Question:** Can we use uv instead of conda as the package manager? It installs and updates dependencies much faster and creates a cross-platform lockfile.

**Answer:** Yes.

**What this means:**

- `pyproject.toml` and `uv.lock` replace `requirements.txt`.
- The [README](../README.md) and the [Dockerfile](../Dockerfile) move to uv.

## 3. Redis instead of nginx as the cache

Decided on 2026-10-28.

**Question:** Can we use Redis instead of nginx as the cache? Open-source nginx can't clear specific cache entries; that requires nginx Plus or a third-party module.

**Answer:** Yes.

**What this means:**

- Redis replaces nginx in [Step 7](roadmap.md#step-7-an-nginx-caching-layer). The requirements stay the same. In "done when", a repeated request is answered from Redis without reading Zarr.
- Cache keys include the dataset version (`{dataset_id}:v{version}:{query hash}`), so a version bump invalidates a dataset's entries without a purge. The version must also change when variables, issues or metadata change. Listing endpoints use a global catalogue version.
- Responses are stored compressed, up to a size limit. When an entry is missing, only one request computes it.
- nginx can still be used as a plain reverse proxy, just not as the cache.

## 4. The serverless fallback is out of scope

Decided on 2026-10-28.

**Question:** The website falls back to `serverless.datalakes-eawag.ch` when the API doesn't respond within 1 second. Is this still needed once the new API has a cache?

**Answer:** Datalakes has no cache today. The serverless functions only read from the database, so the website keeps partly working when the API is overloaded. It's out of scope for now.

**What this means:**

- Nothing to build now.

## 5. Sources: S3 and SWITCHdrive

Decided on 2026-10-28.

**Question:** Some datasets keep their NetCDF files in git rather than S3. Can we stop supporting git as a data source, so we don't need both rclone and git clone/pull?

**Answer:** Yes. Data can move to S3, but there should also be an easier option for people who aren't familiar with buckets. We want to use rclone as the primary tool for accessing data, like Renku does. In the beginning we will support S3 and SWITCHdrive public shares (via WebDAV). Google Drive and OneDrive need credentials, so they're not supported for now.

**What this means:**

- All sources go through rclone, so there is one code path. The server builds each remote on the fly from an allowlist of options per backend, never from user-supplied config or a shared `rclone.conf`. S3 is read anonymously, without access keys.
- SWITCHdrive public shares: read with rclone's WebDAV backend, like Renku's switchDrive shared mode: `:webdav,url='https://drive.switch.ch/public.php/webdav/',vendor=owncloud,user=<share token>:`
  - The owner pastes the share link (`https://drive.switch.ch/index.php/s/<share token>`), and the API stores only the token.
  - Anyone with the link can read the whole share, so it should only contain the dataset's files.
  - Password-protected shares are not supported. A share password is a credential.
- Git-only datasets: datasets in public repositories must move to S3 or SWITCHdrive before the migration.

## 6. Hidden datasets are migrated

Decided on 2026-10-28.

**Question:** Should hidden datasets (`dataportal` is `NULL`) be migrated? `GET /datasets` doesn't list them, but `GET /datasets/:id` still returns them.

**Answer:** Yes. They are a way to have test or work-in-progress datasets on the site that aren't easy to find.

**What this means:**

- All datasets except 1 and 16 (decision 7) are migrated with their IDs, including the 62 hidden ones.
- Replace `dataportal` with a `status` column ([Step 1](roadmap.md#step-1-generalise-the-database)): `published` (listed) or `draft` (not listed, but reachable by ID).
- The 18 hidden datasets that the website forwards to Alplakes become link datasets (decision 7).

## 7. Datasets 1 and 16 are not migrated; link datasets

Decided on 2026-10-28.

**Question:** Datasets 1 (Swiss Lake Morphology) and 16 (ch2018) are listed on the website, but their data lives as static files on the old API server. Should they be kept as static datasets, moved to S3, or dropped?

**Answer:** Exclude them. They will get their own page in a later release. Also add datasets that only link to an external page, without a data source that gets synced.

**What this means:**

- 1 and 16 are left out of the migration and of Step 9's check that every old ID exists.
- Link datasets ([Step 1](roadmap.md#step-1-generalise-the-database)) have the usual metadata and an external URL, but no source, variables, sync, Zarr or availability.

## 8. Embargoed datasets are migrated without embargo

Decided on 2026-10-28.

**Question:** Can the three embargoed datasets (327, 329 and 402) be migrated without embargo?

**Answer:** Yes. No password access is needed.

**What this means:** they are migrated like other hidden datasets, as drafts. Embargo settings and passwords are not migrated.

## 9. Leave out authentication for now?

Proposed since 2026-10-28.

**Question:** Can we leave out authentication for now and add it later in the project? It makes development and testing easier.

**Answer:** Not answered yet.

**What this would mean:**

- All endpoints, including writes, syncs and maintainer endpoints, are open during development. The GitHub login in [app/auth.py](../app/auth.py) is switched off.
- Authentication, including the tokens the Airflow pipelines use to trigger syncs, must be in place before the switch-over.
