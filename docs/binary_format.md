# Binary data format

`GET /datasets/{dataset_id}/data?format=binary` returns the same data as the default JSON, but with the values as typed arrays. It's meant for clients that plot a lot of data, such as the website: the browser reads the values without parsing or copying them.

The media type is `application/vnd.datalakes.data`.

## Layout

All numbers are little-endian.

| Bytes        | What                                                                          |
| ------------ | ----------------------------------------------------------------------------- |
| 0–3          | Length `n` of the header in bytes, as `uint32`                                |
| 4 to 4 + `n` | The header: UTF-8 JSON, padded with spaces so that `4 + n` is a multiple of 8 |
| From 4 + `n` | The arrays, one after the other, each starting at a multiple of 8 bytes       |

## Header

The header is the JSON response, with each `values` replaced by where its array is:

```json
{
  "dataset_id": 448,
  "version": 5,
  "feature_type": "timeSeriesProfile",
  "query": {"variables": ["temp"], "start": "2024-01-01T00:00:00Z", "end": "2024-12-31T23:59:59.999999Z", "depth_min": null, "depth_max": null, "mask": false},
  "coordinates": {
    "time": {"unit": "milliseconds since 1970-01-01T00:00:00Z", "dimensions": ["time"], "values": {"dtype": "float64", "shape": [51240], "offset": 0}},
    "depth": {"unit": "m", "dimensions": ["depth"], "values": {"dtype": "float64", "shape": [48], "offset": 409920}}
  },
  "variables": {
    "temp": {"parameter": "Water temperature", "unit": "degC", "dimensions": ["time", "depth"], "flag_for": null,
             "values": {"dtype": "float32", "shape": [51240, 48], "offset": 410304}}
  }
}
```

- `offset` is counted from the end of the header (byte `4 + n`).
- `shape` follows `dimensions`. A 2-D array is stored row by row: the value at time `i` and depth `j` is at index `i * shape[1] + j`.

## Types

| Array     | `dtype`   | Notes                                                                                              |
| --------- | --------- | -------------------------------------------------------------------------------------------------- |
| Time      | `float64` | Milliseconds since 1970-01-01 UTC, as JavaScript's `Date` takes them                               |
| Depth     | `float64` |                                                                                                    |
| Variables | `float32` | NaN for missing values. About 7 significant digits. |

## Reading it in JavaScript

```js
const response = await fetch(`${api}/datasets/448/data?variables=temp&start=2024-01-01&end=2024-12-31&format=binary`);
const buffer = await response.arrayBuffer();
const length = new DataView(buffer).getUint32(0, true);
const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, length)));

const array = ({ values: { dtype, shape, offset } }) => {
  const Type = dtype === "float64" ? Float64Array : Float32Array;
  return new Type(buffer, 4 + length + offset, shape.reduce((a, b) => a * b, 1));
};
const time = array(header.coordinates.time);  // Float64Array of milliseconds
const temp = array(header.variables.temp);    // Float32Array, row by row
```

The typed arrays are views on the response's buffer, so nothing is copied.
