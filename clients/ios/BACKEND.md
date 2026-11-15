# Capture upload contract

This client follows section 10 of `docs/PLAN.md`. The API is proposed and not implemented here. Configure its HTTPS base URL and a static bearer token in the app. Paths are relative to that base URL.

## Create/recover an upload

`POST /v1/captures`, `Authorization: Bearer <token>`, `Content-Type: application/json`:

```json
{
  "capture_id": "6F1C2A9E-3B4D-4E8F-9A0B-1C2D3E4F5A6B",
  "map_id": "office-floor-3",
  "size_bytes": 36851234,
  "created_unix": 1794651300.123
}
```

Return a 2xx response:

```json
{
  "upload_url": "https://storage.googleapis.com/BUCKET/KEY?SIGNED_PARAMETERS",
  "expires_at": "2026-11-14T23:00:00Z"
}
```

`expires_at` is optional; when present, return an ISO 8601 string. The client requests a fresh URL for every transfer retry rather than guessing expiration. Sign a PUT with `Content-Type: application/zip`. Keep cloud credentials server-side. Authorize the capture/map association before issuing a URL.

**Idempotency:** `capture_id` is generated once on the iPhone. Repeated create calls must return a fresh signed URL for the same capture/object, not create duplicate processing jobs. Replacing that object's bytes on a transfer retry must be safe.

## Transfer

The iPhone uploads the ZIP from disk with a background URLSession PUT. No bearer token is forwarded to storage. The signed URL query is preserved. The transfer succeeds only for an HTTP 2xx response. Expired URLs/failed HTTP transfers back off and return to the create step.

## Confirm

`POST /v1/captures/<capture_id>/complete`, with bearer authorization.

Return `202` (or another 2xx) with:

```json
{"status":"processing"}
```

Confirmation must be idempotent: verify the object and queue processing once. Repeated calls after network uncertainty or app restart return the current status without duplicate jobs. After a successful PUT, the client persists that fact and retries this endpoint independently of the transfer.

## Optional status

`GET /v1/captures/<capture_id>`, with bearer authorization:

```json
{"status":"uploaded"}
```

Valid states: `uploaded`, `processing`, `done`, `failed`. The user refreshes the list to fetch them. No localization coordinate or detector response is needed by this recording client.

401 responses require the user to update the static token. A future refresh endpoint/provider must be agreed before adding automatic token refresh.

## Dataset

The ZIP may contain a UUID-named top-level folder. Locate its `capture.json`; all referenced paths are relative to that directory. Schema version 1 follows section 9 of the plan, with `imu.sample_counts` added for integrity checks and an explicit motion-sign convention. The three CSV files have headers and system-uptime timestamps. Images have no EXIF orientation tag and remain in raw sensor orientation; the backend owns all camera-axis/portrait conversions. See section 13 of the plan.

An available GPS fix has null altitude values when vertical accuracy is invalid; do not substitute MSL altitude in a MultiSet geo hint. Entire `geo` is null if GPS was off/denied/timed out. High-resolution photo fallback is explicit through `photo.high_res: false` and the capture log.

Accept capture sizes based on recording settings; the normal 20-second dataset is estimated at 30–40 MB, with actual bytes provided at create time. ZIP validation should reject unsafe paths and inconsistent file counts/dimensions. Use `Scripts/validate_capture.py` locally for handoff inspection.
