import os
import time
import requests


def UploadVideo(
    server_url: str,
    video_path: str,
    company_name: str,
    sidecar_path: str = None,
    wait_for_processing: bool = True,
    process_timeout: float = 180.0,
) -> str:
    """
    Uploads a video file (and optional zoom sidecar) to the server.
    When background zoom processing is triggered, waits for completion and
    returns the processed video URL (falling back to the raw URL on error).
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found at: {video_path}")

    base_url = server_url.rstrip("/")
    endpoint = f"{base_url}/upload"
    filename = os.path.basename(video_path)

    if not sidecar_path:
        base_no_ext = os.path.splitext(video_path)[0]
        for candidate in [f"{base_no_ext}.zoom.json", f"{video_path}.zoom.json"]:
            if os.path.isfile(candidate):
                sidecar_path = candidate
                break

    with open(video_path, "rb") as vf:
        files = {"file": (filename, vf, "video/webm")}
        data = {"company_name": company_name}

        if sidecar_path and os.path.isfile(sidecar_path):
            with open(sidecar_path, "rb") as sf:
                files["sidecar"] = (os.path.basename(sidecar_path), sf, "application/json")
                response = requests.post(endpoint, files=files, data=data, timeout=60)
        else:
            response = requests.post(endpoint, files=files, data=data, timeout=60)

    response.raise_for_status()
    payload = response.json()

    if wait_for_processing and payload.get("processing_status") == "pending" and payload.get("id") is not None:
        job_endpoint = payload.get("job_url") or f"{base_url}/jobs/{payload['id']}"
        deadline = time.time() + process_timeout
        while time.time() < deadline:
            time.sleep(0.5)
            try:
                job_resp = requests.get(job_endpoint, timeout=15)
                if job_resp.status_code == 200:
                    job_data = job_resp.json()
                    status = job_data.get("status")
                    if status == "completed" and job_data.get("processed_video_url"):
                        return job_data["processed_video_url"]
                    if status == "failed":
                        break
            except Exception:
                pass

    return payload.get("processed_video_url") or payload.get("video_url")


def GetVideoURL(server_url: str, company_name: str) -> str:
    """Returns the video URL (preferring processed video URL if available)."""
    endpoint = f"{server_url.rstrip('/')}/get_video_url"
    response = requests.get(endpoint, params={"company_name": company_name}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return payload.get("processed_video_url") or payload.get("video_url")
