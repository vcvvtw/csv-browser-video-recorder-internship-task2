import json
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from io import BytesIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import web_service


class TestWebServiceUnit(unittest.TestCase):
    def setUp(self):
        web_service.app.config["TESTING"] = True
        self.client = web_service.app.test_client()
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_build_safe_filename(self):
        fn1 = web_service.build_safe_filename("Acme Berlin Tech", "sample.webm")
        self.assertTrue(fn1.startswith("Acme_Berlin_Tech_"))
        self.assertTrue(fn1.endswith(".webm"))

        fn2 = web_service.build_safe_filename("Bavaria Soft Solutions! #1", "rec.mp4")
        self.assertTrue(fn2.startswith("Bavaria_Soft_Solutions_1_"))
        self.assertTrue(fn2.endswith(".mp4"))

        fn3 = web_service.build_safe_filename("   ", "sample.webm")
        self.assertTrue(fn3.startswith("company_"))
        self.assertTrue(fn3.endswith(".webm"))

        # Unsupported extensions must raise ValueError
        with self.assertRaises(ValueError):
            web_service.build_safe_filename("Acme Corp", "malicious.exe")
        with self.assertRaises(ValueError):
            web_service.build_safe_filename("Acme Corp", "archive.zip")

    def test_index_endpoint(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("endpoints", data)

    def test_health_endpoint_ok(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("web_service.get_db", return_value=mock_conn):
            res = self.client.get("/health")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.get_json()["status"], "ok")
            self.assertEqual(res.get_json()["db"], "ok")

    def test_health_endpoint_error(self):
        with patch("web_service.get_db", side_effect=Exception("DB connection refused")):
            res = self.client.get("/health")
            self.assertEqual(res.status_code, 503)
            self.assertEqual(res.get_json()["status"], "error")
            self.assertEqual(res.get_json()["db"], "unavailable")

    def test_get_video_url_missing_param(self):
        res = self.client.get("/get_video_url")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_get_video_url_not_found(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("web_service.get_db", return_value=mock_conn):
            res = self.client.get("/get_video_url?company_name=UnknownCorp")
            self.assertEqual(res.status_code, 404)
            self.assertIn("error", res.get_json())

    def test_get_video_url_success(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 1,
            "company_name": "Acme Berlin Tech",
            "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech_abc12345.webm",
            "processed_video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech_abc12345.zoomed.webm",
            "processing_status": "completed",
        }
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("web_service.get_db", return_value=mock_conn):
            res = self.client.get("/get_video_url?company_name=Acme%20Berlin%20Tech")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["company_name"], "Acme Berlin Tech")
            self.assertTrue(data["video_url"].endswith(".webm"))
            self.assertEqual(data["processing_status"], "completed")

    def test_upload_missing_file_field(self):
        res = self.client.post("/upload", data={"company_name": "Acme Berlin Tech"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_upload_missing_company_name(self):
        data = {
            "file": (BytesIO(b"FAKEWEBMDATA"), "test.webm")
        }
        res = self.client.post("/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_upload_unsupported_format(self):
        data = {
            "file": (BytesIO(b"FAKEEXE"), "test.exe"),
            "company_name": "Acme Berlin Tech"
        }
        res = self.client.post("/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())
        self.assertIn("Unsupported video format", res.get_json()["error"])

    def test_upload_db_error_cleans_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("web_service.get_uploads_dir", return_value=tmpdir):
                with patch("web_service.get_db", side_effect=Exception("DB write failure")):
                    data = {
                        "file": (BytesIO(b"VIDEODATA"), "video.webm"),
                        "company_name": "Fail Company"
                    }
                    res = self.client.post("/upload", data=data, content_type="multipart/form-data")
                    self.assertEqual(res.status_code, 500)
                    self.assertIn("Database error", res.get_json()["error"])
                    # Ensure orphan file was removed
                    self.assertEqual(len(os.listdir(tmpdir)), 0)

    def test_upload_with_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (42,)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur

            sidecar_content = json.dumps({
                "viewport": {"width": 1280, "height": 720},
                "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}]
            }).encode("utf-8")

            with patch("web_service.get_uploads_dir", return_value=tmpdir):
                with patch("web_service.get_db", return_value=mock_conn):
                    with patch("web_service.threading.Thread") as mock_thread:
                        data = {
                            "file": (BytesIO(b"WEBMDATA"), "sample.webm"),
                            "sidecar": (BytesIO(sidecar_content), "sample.zoom.json"),
                            "company_name": "Acme Tech"
                        }
                        res = self.client.post("/upload", data=data, content_type="multipart/form-data")
                        self.assertEqual(res.status_code, 200)
                        payload = res.get_json()
                        self.assertEqual(payload["id"], 42)
                        self.assertEqual(payload["processing_status"], "pending")
                        # While pending, processed_video_url must stay None (avoid 404 before ffmpeg completes)
                        self.assertIsNone(payload["processed_video_url"])
                        self.assertIn("/jobs/42", payload["job_url"])
                        mock_thread.return_value.start.assert_called_once()

    def test_upload_sync_with_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = (99,)
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur

            sidecar_content = json.dumps({
                "viewport": {"width": 1280, "height": 720},
                "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}]
            }).encode("utf-8")

            with patch("web_service.get_uploads_dir", return_value=tmpdir):
                with patch("web_service.get_db", return_value=mock_conn):
                    with patch("web_service.apply_zoom") as mock_apply:
                        data = {
                            "file": (BytesIO(b"WEBMDATA"), "sample.webm"),
                            "sidecar": (BytesIO(sidecar_content), "sample.zoom.json"),
                            "company_name": "Acme Sync Tech",
                            "sync": "1",
                        }
                        res = self.client.post("/upload", data=data, content_type="multipart/form-data")
                        self.assertEqual(res.status_code, 200)
                        payload = res.get_json()
                        self.assertEqual(payload["processing_status"], "completed")
                        self.assertIn("zoomed.webm", payload["processed_video_url"])
                        mock_apply.assert_called_once()

    def test_upload_invalid_sidecar_json_cleans_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data = {
                "file": (BytesIO(b"WEBMDATA"), "sample.webm"),
                "sidecar": "{not valid json",
                "company_name": "Bad Sidecar Co"
            }
            with patch("web_service.get_uploads_dir", return_value=tmpdir):
                res = self.client.post("/upload", data=data, content_type="multipart/form-data")
                self.assertEqual(res.status_code, 400)
                self.assertIn("Invalid sidecar JSON", res.get_json()["error"])
                self.assertEqual(len(os.listdir(tmpdir)), 0)

    def test_process_job_endpoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_video = os.path.join(tmpdir, "test.webm")
            with open(test_video, "wb") as f:
                f.write(b"WEBM")

            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = {
                "id": 1,
                "company_name": "Acme Berlin Tech",
                "file_path": test_video,
                "zoom_sidecar_path": None,
                "video_url": "http://127.0.0.1:8000/media/test.webm",
            }
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur

            with patch("web_service.get_uploads_dir", return_value=tmpdir):
                with patch("web_service.get_db", return_value=mock_conn):
                    with patch("web_service.apply_zoom") as mock_apply:
                        res = self.client.post("/jobs/1/process")
                        self.assertEqual(res.status_code, 200)
                        data = res.get_json()
                        self.assertEqual(data["status"], "completed")
                        self.assertIn("zoomed.webm", data["processed_video_url"])
                        mock_apply.assert_called_once()

    def test_get_job_endpoint(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "id": 7,
            "company_name": "Acme Berlin Tech",
            "processing_status": "completed",
            "video_url": "http://127.0.0.1:8000/media/test.webm",
            "processed_video_url": "http://127.0.0.1:8000/media/test.zoomed.webm",
        }
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("web_service.get_db", return_value=mock_conn):
            res = self.client.get("/jobs/7")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["id"], 7)
            self.assertEqual(data["status"], "completed")

    def test_list_videos(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {
                "id": 1,
                "company_name": "Acme Berlin Tech",
                "file_path": "/app/uploads/Acme_Berlin_Tech_abc12345.webm",
                "video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech_abc12345.webm",
                "processed_file_path": "/app/uploads/Acme_Berlin_Tech_abc12345.zoomed.webm",
                "processed_video_url": "http://127.0.0.1:8000/media/Acme_Berlin_Tech_abc12345.zoomed.webm",
                "processing_status": "completed",
                "created_at": "2026-11-22 12:00:00",
                "updated_at": "2026-11-22 12:00:00"
            }
        ]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        with patch("web_service.get_db", return_value=mock_conn):
            res = self.client.get("/videos")
            self.assertEqual(res.status_code, 200)
            items = res.get_json()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["company_name"], "Acme Berlin Tech")
            self.assertEqual(items[0]["processing_status"], "completed")


if __name__ == "__main__":
    unittest.main()
