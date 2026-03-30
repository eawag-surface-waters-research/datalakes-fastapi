| File                 | Status | Comment                                                                         |
|----------------------|--------|---------------------------------------------------------------------------------|
| auth.js              | Done   |                                                                                 |
| datasets.js          | Done   |                                                                                 |
| issues.js            | Delete |                                                                                 |
| renku.js             | Delete |                                                                                 |
| clean.js             |        | Clean up database and file system when datasets have been removed or partially  |
| download.js          | Delete |                                                                                 |
| logs.js              | Delete |                                                                                 |
| repositories.js      | Done   |                                                                                 |
| contact.js           | Delete |                                                                                 |
| externaldata.js      | Delete |                                                                                 |
| maintenance.js       | Delete |                                                                                 |
| s3zip.js             |        |                                                                                 |
| convert.js           | Delete |                                                                                 |
| files.js             | Delete |                                                                                 |
| monitor.js           | Delete |                                                                                 |
| selectiontables.js   | Done   |                                                                                 |
| data.js              |        | See below                                                                       |
| gitclone.js          | Delete |                                                                                 |
| netcdf.js            | Delete |                                                                                 |
| status.js            | Delete |                                                                                 |
| datalakesmodel.js    | Delete |                                                                                 |
| gitwebhook.js        | Delete |                                                                                 |
| pipeline.js          |        |                                                                                 |
| update.js            |        |                                                                                 |
| datasetparameters.js | Done   |                                                                                 |
| index.js             | Delete |                                                                                 |
| refresh.js           | Delete |                                                                                 |


Current work in progress

Implementing remote.py

User can provide rclone or link (if .bucket is in repo then pre-populate rclone info - but allow edits)

rclone 
- Provide dataset_id, rclone config and prefix
- Add to rclone config and return a list of files
- On front end if there are folders in the list of returned file offer to select a sub folder
- Sync that folder to data / dataset_id 

link
- Provide link and dataset_id 
- Download link unzip and return a list of files

Parse the first netcdf file (structure code to allow other file types in future) 
Return it to the client to add datasetparameters for the dataset

Test that all files can be parsed using the datset parameters and add them to the files table with start and end date 

Create a latest data .json file that is like a cache - upload to R2

Add endpoints for accessing data

Clone to staging - get new data before 
overwriting production else send an sentry error

For 2D datasets create a virtualzarr for each period where depth axis is the same
For profiles create one virtualzarr
Only include parameters from datasetparameters in virtualzarr
