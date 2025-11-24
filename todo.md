| File                 | Status | Comment   |
|----------------------|--------|-----------|
| auth.js              | Done   |           |
| datasets.js          | Done   |           |
| issues.js            | Delete |           |
| renku.js             | Delete |           |
| clean.js             |        |           |
| download.js          | Delete |           |
| logs.js              | Delete |           |
| repositories.js      | Done   |           |
| contact.js           | Delete |           |
| externaldata.js      | Delete |           |
| maintenance.js       |        |           |
| s3zip.js             |        |           |
| convert.js           | Delete |           |
| files.js             | Delete |           |
| monitor.js           | Delete |           |
| selectiontables.js   | Done   |           |
| data.js              |        | See below |
| gitclone.js          | Delete |           |
| netcdf.js            | Delete |           |
| status.js            | Delete |           |
| datalakesmodel.js    | Delete |           |
| gitwebhook.js        | Delete |           |
| pipeline.js          |        |           |
| update.js            |        |           |
| datasetparameters.js | Done   |           |
| index.js             | Delete |           |
| refresh.js           | Delete |           |


Change from a filesystem where we convert from NetCDF to JSON and then 
serve the data to one where we serve the netcdf data directly.

This reduces flexibility e.g. large files will break the system.

Use rclone for data access.
Staging server to prevent read/write conflicts
Use xarray and Virtualzarr and caching to make sending data as fast as possible
Set default time window
Only send one parameter at a time to reduce data flow
Option to return data as csv rather than json

Clone to staging then create virtualzarr - get new data before 
overwriting production else send an sentry error

For 2D datasets create a virtualzarr for each period where depth axis is the same
For profiles create one virtualzarr
Only include parameters from datasetparameters in virtualzarr
