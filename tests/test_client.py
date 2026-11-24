import os
import sys
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
            "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm"
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        # Use an existing sample video
        sample_path = os.path.join(BASE_DIR, "video", "Acme Berlin Tech.webm")
        if not os.path.exists(sample_path):
            sample_path = __file__

        url = UploadVideo("http://127.0.0.1:8000", sample_path, "Acme Berlin Tech")
        self.assertEqual(url, "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm")
        mock_post.assert_called_once()

    @patch("requests.get")
    def test_get_video_url_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "company_name": "Acme Berlin Tech",
            "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm"
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        url = GetVideoURL("http://127.0.0.1:8000", "Acme Berlin Tech")
        self.assertEqual(url, "http://127.0.0.1:8000/media/Acme_Berlin_Tech.webm")
        mock_get.assert_called_once_with(
            "http://127.0.0.1:8000/get_video_url",
            params={"company_name": "Acme Berlin Tech"},
            timeout=15
        )


if __name__ == "__main__":
    unittest.main()
