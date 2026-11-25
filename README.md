# Internship Task 2: Camtasia-Style Zoom Effect for Recorded Videos

Extension of the video recording pipeline and Dockerized web service with a **Camtasia-style zoom effect**:
- **Capture**: Hotkey (F5) and programmatic hook (`window.__zoomAt`) record zoom events `{t, x, y, type}` with an on-screen visual indicator ring during recording into a `<video>.zoom.json` sidecar.
- **Post-processing (`apply_zoom.py`)**: Uses ffmpeg's `zoompan` filter to render smooth zoom-in -> hold -> zoom-out around mouse focus points with cubic smoothstep easing and frame boundary clamping.
- **Docker Integration**: `ffmpeg` is integrated into the web service container; uploaded videos with sidecars are automatically post-processed and updated in PostgreSQL.

---

## Architecture

```text
+----------------------------------------------------------------------------------+
| 1. Capture (qscript.py)                                                          |
|    - Playwright browser recording (1280x720 @ 25fps)                            |
|    - F5 keypress / window.__zoomAt(x, y) toggles zoom in/out                     |
|    - Colored ring indicator reflects toggle state                                |
|    - Sidecar written to <video>.zoom.json when events exist                      |
+----------------------------------------------------------------------------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
| 2. Post-Processing Engine (apply_zoom.py)                                        |
|    - Reads sidecar metadata (viewport, fps, zoom_factor, ramp_sec, events)       |
|    - Normalizes events and pairs 'in' -> 'out' intervals (supports open-ended)   |
|    - Normalizes PTS/CFR & 2x supersamples before zoompan to prevent shimmer      |
|    - Maps mouse coords proportionally & clamps crop: clip(px*iw-iw/(2*z), ...)  |
|    - Evaluates smoothstep easing (3*p^2 - 2*p^3) for smooth transitions         |
|    - Executes or previews (--dry-run) ffmpeg VP9 command                         |
+----------------------------------------------------------------------------------+
                                         |
                                         v
+----------------------------------------------------------------------------------+
| 3. Docker Service Integration (Task 1 + Task 2)                                  |
|    - Debian container with Apache 2.4, mod_wsgi, Flask, and ffmpeg               |
|    - POST /upload receives video + sidecar and starts async zoom worker          |
|    - POST /jobs/<id>/process triggers zoom post-processing on demand             |
|    - PostgreSQL tracks processed_video_url and processing_status                 |
+----------------------------------------------------------------------------------+
```

---

## Directory Structure

```text
internship_task2/
├── Dockerfile                  # Container definition with Apache, mod_wsgi & ffmpeg
├── docker-compose.yml          # Multi-container orchestration (web + db)
├── apache.conf                 # Apache VirtualHost & WSGI daemon configuration
├── entrypoint.sh               # Environment export and Apache startup
├── web_service.wsgi            # WSGI entry point
├── web_service.py              # Flask service with PostgreSQL & zoom job processing
├── apply_zoom.py               # Ffmpeg zoompan post-processing CLI and engine
├── qscript.py                  # Browser recording scenario with zoom events & F5
├── video_client.py             # Client API (UploadVideo, GetVideoURL with sidecar)
├── capture_video.py            # Automation recorder CLI
├── input.csv                   # Input company dataset
├── requirements.txt            # Server dependencies (Flask, psycopg2-binary, requests, pytest)
├── requirements-client.txt     # Client dependencies (requests, playwright, pytest)
├── .env.example                # Environment variables template
├── .gitattributes              # LF line-ending and binary file rules
├── .gitignore                  # Git ignore rules
├── README.md                   # Documentation
├── tests/                      # Automated test suite
│   ├── test_zoom.py            # Pytest tests for zoom filter math & edge cases
│   ├── test_service.py         # Tests for Flask service, DB & job endpoints
│   └── test_client.py          # Tests for client API
└── video/                      # Recorded video samples (.webm)
    ├── Acme Berlin Tech.webm
    └── Bavaria Soft Solutions.webm
```

---

## Zoom Capture (during recording)

- **F5 Hotkey**: First press marks the start of the zoom-in at current mouse position; second press marks the start of the zoom-out.
- **Programmatic Hook**: `window.__zoomAt(x, y)` triggers the toggle programmatically from scenarios.
- **Visual Ring**: Shows an on-screen animated ring (green expanding for zoom-in, red contracting for zoom-out).
- **Sidecar file**: Saved as `<video>.zoom.json` alongside the video only when at least one event exists:

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

---

## CLI Usage (`apply_zoom.py`)

Preview ffmpeg command without encoding:
```bash
python apply_zoom.py --video video/ACME_GmbH.webm --dry-run
```

Render zoomed video:
```bash
python apply_zoom.py --video video/ACME_GmbH.webm
```

Specify custom sidecar, output, or ffmpeg binary path:
```bash
python apply_zoom.py --video video/ACME_GmbH.webm --sidecar video/custom.zoom.json --output video/output.webm --ffmpeg /usr/bin/ffmpeg
```

---

## Running with Docker Compose

### 1. Build and Start

```bash
docker compose up --build -d
```

### 2. Verify Services

```bash
docker compose ps
docker compose logs -f web
```

### 3. API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Multipart upload with `file` and optional `sidecar` (async by default, pass `?sync=1` to wait) |
| `POST` | `/jobs/<id>/process` | Triggers ffmpeg zoom rendering on a video record |
| `GET` | `/jobs/<id>` | Returns processing status (`pending`, `completed`, `failed`) |
| `GET` | `/get_video_url?company_name=<name>` | Retrieves public URLs (original & processed) |
| `GET` | `/videos` | Lists all video records with processing metadata |
| `GET` | `/media/<filename>` | Serves raw or zoomed video files directly |
| `GET` | `/health` | Database and service health check |

---

## Running the Automated Test Suite

Run all tests with `pytest`:
```bash
pytest -v
```

Or run via `python -m pytest`:
```bash
python -m pytest tests/ -v
```

Or run with standard `unittest`:
```bash
python -m unittest discover -s tests -v
```

Test coverage includes:
- Ramp duration frame calculations across frame rates.
- Coordinate clamping to frame boundaries for center, corners, and out-of-bounds inputs.
- Smoothstep easing (start, mid-ramp, hold, ramp-out).
- Multiple non-overlapping in/out pairs.
- Open-ended zoom (in with no subsequent out holds to video end).
- Sidecar parsing and normalization of legacy sidecars without `type`.
- "No events -> copy unchanged" fallback path.
- Dry-run command formatting.
- Flask endpoints, async background worker, and PostgreSQL persistence.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `db` | PostgreSQL service host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `videos` | Database name |
| `DB_USER` | `video_user` | Database username |
| `DB_PASSWORD` | `video_pass` | Database password |
| `DB_CONNECT_TIMEOUT` | `10` | Database connection timeout in seconds |
| `UPLOADS_DIR` | `/app/uploads` | Path to media directory inside container |
