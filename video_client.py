import os
import requests


def UploadVideo(server_url: str, video_path: str, company_name: str, sidecar_path: str = None) -> str:
    """Uploads a video file (and optional zoom sidecar) to the server and returns its public URL."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found at: {video_path}")

    endpoint = f"{server_url.rstrip('/')}/upload"
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
    return payload.get("processed_video_url") or payload.get("video_url")


def GetVideoURL(server_url: str, company_name: str) -> str:
    """Returns the video URL (or processed video URL) for the given company name."""
    endpoint = f"{server_url.rstrip('/')}/get_video_url"
    response = requests.get(endpoint, params={"company_name": company_name}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    return payload.get("processed_video_url") or payload.get("video_url")
