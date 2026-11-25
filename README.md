# Browser Video Recorder with Post-Processed Zoom

Playwright-based browser recorder extended with zoom event capture (`F5` / `window.__zoomAt`), `ffmpeg` `zoompan` post-processing (`apply_zoom.py`), and automatic background processing in a Dockerized Flask + PostgreSQL service.

## Pipeline Overview

1. **Capture (`qscript.py`, `capture_video.py`)**: Records `1280x720` browser automation runs to `.webm`, shows a brief green/red toggle indicator ring, calibrates event timestamps to video frame start, and writes `<video>.zoom.json` when zoom events exist.
2. **Post-processing (`apply_zoom.py`)**: Parses `.zoom.json`, pairs `in`/`out` intervals (supporting multiple intervals and open-ended trailing zooms), applies `ffmpeg` `zoompan` with cubic smoothstep easing and frame-bound clamping, or copies unchanged when no events exist.
3. **Service (`web_service.py`, `video_client.py`)**: Accepts `.webm` + optional `.zoom.json` uploads, renders zoomed output in background or via `POST /jobs/<id>/process`, and stores `processed_video_url` and `processing_status` in PostgreSQL.

## Files

- `qscript.py` — Playwright recorder (`CaptureVideo_Magento`), overlay cursor/callout/ripple, zoom capture (`F5`, `window.__zoomAt`)
- `apply_zoom.py` — `ffmpeg` `zoompan` filter generator and CLI
- `capture_video.py` — CSV runner with optional upload and preview
- `video_client.py` — HTTP client (`UploadVideo`, `GetVideoURL`)
- `web_service.py` — Flask API + PostgreSQL persistence
- `Dockerfile`, `docker-compose.yml`, `apache.conf`, `entrypoint.sh`, `web_service.wsgi` — Docker deployment (`web` + `db`)
- `tests/` — unit and Playwright integration tests

## Sidecar Format

Written as `<video>.zoom.json` only when at least one zoom event was recorded:

```json
{
  "viewport": {"width": 1280, "height": 720},
  "fps": 25,
  "zoom_factor": 2.0,
  "ramp_sec": 0.5,
  "events": [
    {"t": 3.0, "x": 640, "y": 360, "type": "in"},
    {"t": 8.0, "x": 640, "y": 360, "type": "out"}
  ]
}
```

## CLI Usage

Recording from CSV and uploading to local service:

```bash
python capture_video.py --csv input.csv --server http://127.0.0.1:8000
```

Applying zoom manually with `apply_zoom.py`:

```bash
python apply_zoom.py --video "video/Acme Berlin Tech.webm" --dry-run
python apply_zoom.py --video "video/Acme Berlin Tech.webm"
python apply_zoom.py --video "video/Acme Berlin Tech.webm" --sidecar custom.zoom.json --output out.webm --ffmpeg /usr/bin/ffmpeg
```

## Docker Compose

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f web
```

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Multipart upload (`file`, `company_name`, optional `sidecar`; pass `?sync=1` for blocking render) |
| `POST` | `/jobs/<id>/process` | Trigger `ffmpeg` zoom rendering for a stored record |
| `GET` | `/jobs/<id>` | Poll processing status (`pending`, `completed`, `failed`, `none`) |
| `GET` | `/get_video_url?company_name=<name>` | Return raw and processed video URLs |
| `GET` | `/videos` | List all stored video records |
| `GET` | `/media/<filename>` | Serve raw or `.zoomed.webm` media files |
| `GET` | `/health` | Service and database health check |

## Tests

```bash
pytest -v
docker compose exec web pytest -v
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `db` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `videos` | Database name |
| `DB_USER` | `video_user` | Database user |
| `DB_PASSWORD` | `video_pass` | Database password |
| `DB_CONNECT_TIMEOUT` | `10` | Connection timeout in seconds |
| `UPLOADS_DIR` | `/app/uploads` | Media storage path inside container |
