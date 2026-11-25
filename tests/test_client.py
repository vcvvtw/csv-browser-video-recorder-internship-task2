import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from video_client import UploadVideo, GetVideoURL


class TestVideoClient(unittest.TestCase):
    def test_upload_missing_file_raises_error(self):
        with self.assertRaises(FileNotFoundError):
            UploadVideo("http://127.0.0.1:8000", "non_existent_file.webm", "Acme Berlin Tech")

    @patch("requests.post")
    def test_upload_video_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "company_name": "Acme Berlin Tech",
            "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm",
            "processing_status": "none",
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        sample_path = os.path.join(BASE_DIR, "video", "Acme Berlin Tech.webm")
        if not os.path.exists(sample_path):
            sample_path = __file__

        url = UploadVideo("http://127.0.0.1:8000", sample_path, "Acme Berlin Tech")
        self.assertEqual(url, "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm")
        mock_post.assert_called_once()

    @patch("time.sleep")
    @patch("requests.get")
    @patch("requests.post")
    def test_upload_video_with_sidecar_polls_until_completed(self, mock_post, mock_get, _mock_sleep):
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = {
            "id": 9,
            "company_name": "Zoom Co",
            "video_url": "http://127.0.0.1:8000/media/Zoom_Co_12345678.webm",
            "processed_video_url": None,
            "processing_status": "pending",
            "job_url": "http://127.0.0.1:8000/jobs/9",
        }
        mock_post.return_value = mock_post_resp

        pending_job = MagicMock(status_code=200)
        pending_job.json.return_value = {"id": 9, "status": "pending", "processed_video_url": None}

        done_job = MagicMock(status_code=200)
        done_job.json.return_value = {
            "id": 9,
            "status": "completed",
            "processed_video_url": "http://127.0.0.1:8000/media/Zoom_Co_12345678.zoomed.webm",
        }
        mock_get.side_effect = [pending_job, done_job]

        with tempfile.TemporaryDirectory() as tmpdir:
            vid = os.path.join(tmpdir, "Zoom Co.webm")
            sidecar = os.path.join(tmpdir, "Zoom Co.zoom.json")
            with open(vid, "wb") as f:
                f.write(b"WEBM")
            with open(sidecar, "w") as f:
                json.dump({"events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}]}, f)

            url = UploadVideo("http://127.0.0.1:8000", vid, "Zoom Co")
            self.assertEqual(url, "http://127.0.0.1:8000/media/Zoom_Co_12345678.zoomed.webm")
            # Verify sidecar was attached in multipart upload
            posted_files = mock_post.call_args.kwargs["files"]
            self.assertIn("file", posted_files)
            self.assertIn("sidecar", posted_files)
            self.assertEqual(mock_get.call_count, 2)

    @patch("requests.get")
    def test_get_video_url_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "company_name": "Acme Berlin Tech",
            "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm",
            "processed_video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech.zoomed.webm",
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        url = GetVideoURL("http://127.0.0.1:8000", "Acme Berlin Tech")
        self.assertEqual(url, "http://127.0.0.1:8000/media/Acme_Berlin_Tech.zoomed.webm")
        mock_get.assert_called_once_with(
            "http://127.0.0.1:8000/get_video_url",
            params={"company_name": "Acme Berlin Tech"},
            timeout=15
        )


if __name__ == "__main__":
    unittest.main()
