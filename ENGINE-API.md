# Engine API — yt-dlp engine (service `cobalt-engine`)

Base URL: `https://cobalt-engine-w87i.onrender.com`
(Render free tier: the service sleeps when idle; the first request after
that can take ~50 s to cold-start. It also has 512 MB RAM, so downloads
are processed **one at a time**.)

## Auth

Every route except `GET /` requires:

```
Authorization: Api-Key <key>
```

(`Bearer <key>` is accepted as an alias.) Valid keys are the keys of the
JSON object stored in the service's `API_KEYS_JSON` env var (dashboard
only, never in this repo). A missing/invalid key gets:

```json
HTTP 401
{"status": "error", "error": "unauthorized"}
```

## GET / — health (no auth)

```json
HTTP 200
{
  "status": "ok",
  "engine": "yt-dlp",
  "version": "2026.10.26",
  "node": "v24.x.x",
  "cookies": true,
  "busy": false
}
```

`cookies` says whether a converted `/cookies.txt` exists (i.e. whether
`YT_COOKIES_JSON` was set); `busy` says whether a download is running.

## POST /download

Request JSON:

```json
{
  "url": "https://www.youtube.com/watch?v=<id>",
  "mode": "audio",
  "bitrate": "64"
}
```

| field     | type   | required | meaning |
|-----------|--------|----------|---------|
| `url`     | string | yes      | http(s) page URL (YouTube watch URL) |
| `mode`    | string | no       | `"audio"` (default) or `"video"` |
| `bitrate` | string | audio    | target audio bitrate in kbps, e.g. `"64"`, `"128"` (a JSON number is also accepted; clamped to 16–320; default `"128"`) |
| `quality` | number | video    | maximum video height in px, e.g. `720` (clamped to 144–2160; default `720`) |

Format selection: audio picks the best audio-only format at or under
`bitrate` (fallback: best audio overall); video picks the best
video+audio pair at or under `quality` and merges to MP4 (fallback: best
single file at or under `quality`). Downloads run through yt-dlp with
the service's cookies and the Node EJS challenge solver.

Success — the response is returned only after the download has finished
server-side, so allow a generous client timeout (a 44-minute audio took
~40 s of transfer once extraction succeeded; long/large videos take
longer):

```json
HTTP 200
{
  "status": "ok",
  "url": "https://cobalt-engine-w87i.onrender.com/file/<token>",
  "filename": "media.m4a",
  "size": 42576033,
  "expires_in": 3600
}
```

Errors:

```json
HTTP 429  {"status": "error", "error": "busy"}            // another download is running; retry later
HTTP 400  {"status": "error", "error": "bad_url" | "bad_mode" | "bad_bitrate" | "bad_quality" | "bad_json"}
HTTP 401  {"status": "error", "error": "unauthorized"}
HTTP 502  {"status": "error", "error": "download_failed", "detail": "<tail of yt-dlp output, secrets scrubbed>"}
HTTP 504  {"status": "error", "error": "timeout"}         // download exceeded 1200 s
```

## GET /file/<token>

Same auth header required. Streams the file produced by one
`/download` call:

- `200` with `Content-Length` = exact byte size, `Content-Type` guessed
  from the extension, `Content-Disposition: attachment`.
- **One-shot:** the token is consumed by the first successful fetch and
  the file is deleted right after serving.
- **Lifetime:** unfetched files expire after **3600 s (1 h)** and are
  deleted; an expired/unknown token gets
  `404 {"status": "error", "error": "not_found"}`.
