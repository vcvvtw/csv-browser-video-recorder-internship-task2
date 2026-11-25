import hashlib
import json
import os
import re
import threading
from flask import Flask, g, jsonify, request, send_from_directory, has_request_context
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.middleware.proxy_fix import ProxyFix

from apply_zoom import apply_zoom

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWED_EXTENSIONS = {".webm", ".mp4"}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

_schema_lock = threading.Lock()
_schema_initialized = False


def get_uploads_dir() -> str:
    env = request.environ if has_request_context() else os.environ
    return env.get("UPLOADS_DIR", os.environ.get("UPLOADS_DIR", os.path.join(BASE_DIR, "uploads")))


def get_db_config() -> dict:
    env = request.environ if has_request_context() else os.environ
    return {
        "host": env.get("DB_HOST", os.environ.get("DB_HOST", "db")),
        "port": int(env.get("DB_PORT", os.environ.get("DB_PORT", "5432"))),
        "dbname": env.get("DB_NAME", os.environ.get("DB_NAME", "videos")),
        "user": env.get("DB_USER", os.environ.get("DB_USER", "video_user")),
        "password": env.get("DB_PASSWORD", os.environ.get("DB_PASSWORD", "video_pass")),
        "connect_timeout": int(env.get("DB_CONNECT_TIMEOUT", os.environ.get("DB_CONNECT_TIMEOUT", "10"))),
    }


def build_safe_filename(company_name: str, original_filename: str) -> str:
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported video format. Allowed: .webm, .mp4")

    slug = re.sub(r"[^\w\-]+", "_", company_name.strip(), flags=re.UNICODE).strip("_")
    if not slug:
        slug = "company"

    digest = hashlib.sha256(company_name.strip().encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{digest}{ext}"


def _ensure_schema(conn) -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_lock:
        if _schema_initialized:
            return
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    id SERIAL PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    processed_file_path TEXT,
                    processed_video_url TEXT,
                    zoom_sidecar_path TEXT,
                    processing_status TEXT NOT NULL DEFAULT 'none',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_file_path TEXT;")
            cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processed_video_url TEXT;")
            cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS zoom_sidecar_path TEXT;")
            cur.execute("ALTER TABLE videos ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'none';")
        conn.commit()
        _schema_initialized = True


def get_db():
    if "db" not in g:
        conn = psycopg2.connect(**get_db_config())
        _ensure_schema(conn)
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    conn = g.pop("db", None)
    if conn is not None and not conn.closed:
        if error is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        conn.close()


def _async_process_zoom(
    video_id: int,
    file_path: str,
    sidecar_path: str,
    dest_zoomed_path: str,
    processed_url: str,
    db_config: dict,
):
    """Background worker to render Camtasia-style zoom using ffmpeg."""
    try:
        apply_zoom(file_path, sidecar_path=sidecar_path, output_path=dest_zoomed_path)
        conn = psycopg2.connect(**db_config)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE videos
                    SET processed_file_path = %s,
                        processed_video_url = %s,
                        processing_status = 'completed',
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (dest_zoomed_path, processed_url, video_id),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        app.logger.error("Background zoom processing failed for video ID %s: %s", video_id, exc)
        try:
            conn = psycopg2.connect(**db_config)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE videos
                        SET processing_status = 'failed',
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (video_id,),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass


def init_db() -> None:
    uploads_dir = get_uploads_dir()
    os.makedirs(uploads_dir, exist_ok=True)

    conn = psycopg2.connect(**get_db_config())
    try:
        _ensure_schema(conn)
    finally:
        conn.close()


@app.get("/")
def index():
    return jsonify(
        {
            "service": "video-service",
            "status": "ok",
            "endpoints": [
                "POST /upload",
                "GET /get_video_url?company_name=<name>",
                "GET /media/<filename>",
                "GET /videos",
                "POST /jobs/<id>/process",
                "GET /jobs/<id>",
                "GET /health",
            ],
        }
    ), 200


@app.get("/health")
def health():
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return jsonify({"status": "ok", "db": "ok"}), 200
    except Exception as exc:
        app.logger.error("Health check DB failure: %s", exc)
        return jsonify({"status": "error", "db": "unavailable"}), 503


@app.post("/upload")
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Missing 'file' field"}), 400

    upload_file = request.files["file"]
    if not upload_file or not upload_file.filename:
        return jsonify({"error": "Missing filename"}), 400

    company_name = request.form.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "Missing 'company_name' field"}), 400

    try:
        filename = build_safe_filename(company_name, upload_file.filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    uploads_dir = get_uploads_dir()
    os.makedirs(uploads_dir, exist_ok=True)
    dest_path = os.path.abspath(os.path.join(uploads_dir, filename))
    video_url = f"{request.host_url.rstrip('/')}/media/{filename}"

    upload_file.save(dest_path)

    sidecar_dest_path = None
    sidecar_file = request.files.get("sidecar") or request.files.get("zoom_json")
    sidecar_text = request.form.get("sidecar")

    base_dest_no_ext = os.path.splitext(dest_path)[0]
    default_sidecar_path = f"{base_dest_no_ext}.zoom.json"

    if sidecar_file and sidecar_file.filename:
        sidecar_dest_path = default_sidecar_path
        sidecar_file.save(sidecar_dest_path)
    elif sidecar_text and sidecar_text.strip():
        try:
            parsed = json.loads(sidecar_text)
        except Exception:
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
            return jsonify({"error": "Invalid sidecar JSON payload"}), 400
        sidecar_dest_path = default_sidecar_path
        with open(sidecar_dest_path, "w", encoding="utf-8") as sf:
            json.dump(parsed, sf, indent=2)
    else:
        for stale in (default_sidecar_path, f"{dest_path}.zoom.json"):
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass

    zoomed_filename = f"{os.path.basename(base_dest_no_ext)}.zoomed.webm"
    dest_zoomed_path = os.path.join(uploads_dir, zoomed_filename)
    processed_url = f"{request.host_url.rstrip('/')}/media/{zoomed_filename}"
    initial_status = "pending" if sidecar_dest_path else "none"
    sync_mode = (request.args.get("sync") or request.form.get("sync") or "").lower() in ("1", "true", "yes")

    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO videos (
                    company_name, file_path, video_url,
                    processed_file_path, processed_video_url,
                    zoom_sidecar_path, processing_status,
                    created_at, updated_at
                )
                VALUES (%s, %s, %s, NULL, NULL, %s, %s, NOW(), NOW())
                ON CONFLICT (company_name) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    video_url = EXCLUDED.video_url,
                    processed_file_path = NULL,
                    processed_video_url = NULL,
                    zoom_sidecar_path = EXCLUDED.zoom_sidecar_path,
                    processing_status = EXCLUDED.processing_status,
                    updated_at = NOW()
                RETURNING id;
                """,
                (company_name, dest_path, video_url, sidecar_dest_path, initial_status),
            )
            video_id = cur.fetchone()[0]
        conn.commit()
    except Exception as exc:
        app.logger.error("Failed to save video metadata for %s: %s", company_name, exc)
        for cleanup_path in (dest_path, sidecar_dest_path):
            if cleanup_path and os.path.exists(cleanup_path):
                try:
                    os.remove(cleanup_path)
                except OSError:
                    pass
        return jsonify({"error": "Database error while saving video metadata"}), 500

    final_status = initial_status
    final_processed_url = None

    if sidecar_dest_path:
        if sync_mode:
            try:
                apply_zoom(dest_path, sidecar_path=sidecar_dest_path, output_path=dest_zoomed_path)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE videos
                        SET processed_file_path = %s,
                            processed_video_url = %s,
                            processing_status = 'completed',
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (dest_zoomed_path, processed_url, video_id),
                    )
                conn.commit()
                final_status = "completed"
                final_processed_url = processed_url
            except Exception as exc:
                app.logger.error("Sync zoom processing failed for video ID %s: %s", video_id, exc)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE videos SET processing_status = 'failed', updated_at = NOW() WHERE id = %s;",
                        (video_id,),
                    )
                conn.commit()
                final_status = "failed"
        else:
            db_config = get_db_config()
            worker = threading.Thread(
                target=_async_process_zoom,
                args=(video_id, dest_path, sidecar_dest_path, dest_zoomed_path, processed_url, db_config),
                daemon=True,
            )
            worker.start()

    return jsonify({
        "id": video_id,
        "company_name": company_name,
        "video_url": video_url,
        "processed_video_url": final_processed_url,
        "processing_status": final_status,
        "job_url": f"{request.host_url.rstrip('/')}/jobs/{video_id}",
    }), 200


@app.post("/jobs/<int:job_id>/process")
def process_job(job_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM videos WHERE id = %s;", (job_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Video record not found"}), 404

    file_path = row["file_path"]
    sidecar_path = row.get("zoom_sidecar_path")
    if not sidecar_path or not os.path.exists(sidecar_path):
        base_no_ext = os.path.splitext(file_path)[0]
        for candidate in [f"{base_no_ext}.zoom.json", f"{file_path}.zoom.json"]:
            if os.path.exists(candidate):
                sidecar_path = candidate
                break

    base_no_ext = os.path.splitext(file_path)[0]
    zoomed_filename = f"{os.path.basename(base_no_ext)}.zoomed.webm"
    dest_zoomed_path = os.path.join(get_uploads_dir(), zoomed_filename)
    processed_url = f"{request.host_url.rstrip('/')}/media/{zoomed_filename}"

    try:
        apply_zoom(file_path, sidecar_path=sidecar_path, output_path=dest_zoomed_path)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE videos
                SET processed_file_path = %s,
                    processed_video_url = %s,
                    processing_status = 'completed',
                    updated_at = NOW()
                WHERE id = %s;
                """,
                (dest_zoomed_path, processed_url, job_id),
            )
        conn.commit()
        return jsonify({
            "id": job_id,
            "status": "completed",
            "processed_video_url": processed_url,
        }), 200
    except Exception as exc:
        app.logger.error("Failed processing video ID %s: %s", job_id, exc)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE videos SET processing_status = 'failed', updated_at = NOW() WHERE id = %s;",
                (job_id,),
            )
        conn.commit()
        return jsonify({"error": "Video zoom processing failed"}), 500


@app.get("/jobs/<int:job_id>")
def get_job(job_id: int):
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM videos WHERE id = %s;", (job_id,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Video record not found"}), 404

    return jsonify({
        "id": row["id"],
        "company_name": row["company_name"],
        "status": row.get("processing_status", "none"),
        "video_url": row["video_url"],
        "processed_video_url": row.get("processed_video_url"),
    }), 200


@app.get("/get_video_url")
def get_video_url():
    company_name = request.args.get("company_name", "").strip()
    if not company_name:
        return jsonify({"error": "Missing 'company_name' query parameter"}), 400

    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, company_name, video_url, processed_video_url, processing_status
            FROM videos
            WHERE company_name = %s;
            """,
            (company_name,),
        )
        row = cur.fetchone()

    if row is None:
        return jsonify({"error": "Video not found"}), 404

    return jsonify({
        "id": row["id"],
        "company_name": row["company_name"],
        "video_url": row["video_url"],
        "processed_video_url": row.get("processed_video_url"),
        "processing_status": row.get("processing_status", "none"),
    }), 200


@app.get("/media/<path:filename>")
def media(filename):
    return send_from_directory(get_uploads_dir(), filename)


@app.get("/videos")
def list_videos():
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                id,
                company_name,
                file_path,
                video_url,
                processed_file_path,
                processed_video_url,
                processing_status,
                to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') AS created_at,
                to_char(updated_at, 'YYYY-MM-DD HH24:MI:SS') AS updated_at
            FROM videos
            ORDER BY id ASC;
            """
        )
        rows = cur.fetchall()

    return jsonify([dict(row) for row in rows]), 200


if __name__ == "__main__":
    init_db()
    app.run(
        host=os.environ.get("FLASK_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_PORT", "8000")),
        threaded=True,
    )
