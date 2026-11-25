import json
import os
import sys
import tempfile
import time
import pytest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import apply_zoom


def test_ramp_frame_calculation():
    """Verify ramp duration to frame calculation across common frame rates."""
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.5, fps=25.0) in (12, 13)
    assert apply_zoom.calculate_ramp_frames(ramp_sec=1.0, fps=30.0) == 30
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.2, fps=25.0) == 5
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.0, fps=25.0) == 1


def test_mouse_to_video_mapping():
    """Verify 1:1 mapping and proportional scaling across resolutions."""
    # 1:1 viewport == video
    vx, vy = apply_zoom.map_mouse_to_video(640, 360, 1280, 720, 1280, 720)
    assert (vx, vy) == (640.0, 360.0)

    # Proportional 1.5x (1280x720 -> 1920x1080)
    vx2, vy2 = apply_zoom.map_mouse_to_video(640, 360, 1280, 720, 1920, 1080)
    assert (vx2, vy2) == (960.0, 540.0)

    # Clamping out-of-range mouse coords
    vx3, vy3 = apply_zoom.map_mouse_to_video(2000, -50, 1280, 720, 1280, 720)
    assert (vx3, vy3) == (1280.0, 0.0)


def test_crop_clamping_center():
    """Verify centered coordinates at 2.0x zoom on 1280x720 viewport."""
    iw, ih = 1280.0, 720.0
    x, y = apply_zoom.clamp_crop(px=640.0, py=360.0, iw=iw, ih=ih, zoom=2.0)
    assert x == 320.0
    assert y == 180.0


def test_crop_clamping_boundaries():
    """Verify crop rectangle strictly stays within frame bounds when focus is at extreme edges."""
    iw, ih = 1280.0, 720.0

    # Top-left corner
    x, y = apply_zoom.clamp_crop(px=0.0, py=0.0, iw=iw, ih=ih, zoom=2.0)
    assert x == 0.0
    assert y == 0.0

    # Bottom-right corner
    x, y = apply_zoom.clamp_crop(px=1280.0, py=720.0, iw=iw, ih=ih, zoom=2.0)
    assert x == 640.0
    assert y == 360.0

    # Out of bounds coordinates (negative)
    x, y = apply_zoom.clamp_crop(px=-200.0, py=-100.0, iw=iw, ih=ih, zoom=2.0)
    assert x == 0.0
    assert y == 0.0

    # Out of bounds coordinates (large positive)
    x, y = apply_zoom.clamp_crop(px=2500.0, py=1800.0, iw=iw, ih=ih, zoom=2.0)
    assert x == 640.0
    assert y == 360.0


def test_sidecar_parsing_valid():
    """Verify parsing of standard sidecar structure."""
    raw = {
        "viewport": {"width": 1920, "height": 1080},
        "fps": 30,
        "zoom_factor": 2.5,
        "ramp_sec": 0.4,
        "events": [
            {"t": 1.5, "x": 500, "y": 400, "type": "in"},
            {"t": 5.0, "x": 500, "y": 400, "type": "out"},
        ],
    }
    parsed = apply_zoom.parse_sidecar(raw)
    assert parsed["viewport"] == {"width": 1920, "height": 1080}
    assert parsed["fps"] == 30.0
    assert parsed["zoom_factor"] == 2.5
    assert parsed["ramp_sec"] == 0.4
    assert len(parsed["events"]) == 2
    assert parsed["events"][0]["type"] == "in"
    assert parsed["events"][1]["type"] == "out"


def test_sidecar_parsing_legacy_without_type():
    """Verify backwards-compatibility for older sidecars lacking the 'type' field."""
    raw = {
        "viewport": {"width": 1280, "height": 720},
        "events": [
            {"t": 2.0, "x": 640, "y": 360},
            {"t": 7.0, "x": 640, "y": 360},
            {"t": 10.0, "x": 300, "y": 200},
            {"t": 14.0, "x": 300, "y": 200},
        ],
    }
    parsed = apply_zoom.parse_sidecar(raw)
    events = parsed["events"]
    assert len(events) == 4
    assert events[0]["type"] == "in"
    assert events[1]["type"] == "out"
    assert events[2]["type"] == "in"
    assert events[3]["type"] == "out"


def test_multiple_in_out_pairs():
    """Verify pairing of multiple non-overlapping zoom intervals."""
    events = [
        {"t": 2.0, "x": 640, "y": 360, "type": "in"},
        {"t": 5.0, "x": 640, "y": 360, "type": "out"},
        {"t": 8.0, "x": 300, "y": 200, "type": "in"},
        {"t": 11.0, "x": 300, "y": 200, "type": "out"},
    ]
    pairs = apply_zoom.pair_zoom_events(events)
    assert len(pairs) == 2
    assert pairs[0]["in"]["t"] == 2.0
    assert pairs[0]["out"]["t"] == 5.0
    assert pairs[1]["in"]["t"] == 8.0
    assert pairs[1]["out"]["t"] == 11.0


def test_unclosed_in_does_not_shadow_next_pair():
    """Verify an unclosed 'in' followed by another 'in' yields cleanly at next pair start."""
    events = [
        {"t": 2.0, "x": 100, "y": 100, "type": "in"},
        {"t": 6.0, "x": 900, "y": 500, "type": "in"},
        {"t": 10.0, "x": 900, "y": 500, "type": "out"},
    ]
    pairs = apply_zoom.pair_zoom_events(events)
    assert len(pairs) == 2

    # During first interval
    z1, px1, py1, active1 = apply_zoom.calculate_zoom_at_time(4.0, pairs, zoom_factor=2.0, ramp_sec=0.5)
    assert active1 and z1 == 2.0 and px1 == 100 and py1 == 100

    # During second interval: must focus on (900, 500), not shadowed by first open pair
    z2, px2, py2, active2 = apply_zoom.calculate_zoom_at_time(7.0, pairs, zoom_factor=2.0, ramp_sec=0.5)
    assert active2 and z2 == 2.0 and px2 == 900 and py2 == 500

    # Verify filter expression bounds pair 0 to next_t_in=6.0
    filt = apply_zoom.generate_zoompan_filter({"viewport": {"width": 1280, "height": 720}, "events": events})
    assert "between(it,2.0,6.0)" in filt


def test_open_ended_zoom():
    """Verify open-ended zoom (in with no subsequent out) holds to end of recording."""
    events = [
        {"t": 3.0, "x": 640, "y": 360, "type": "in"}
    ]
    pairs = apply_zoom.pair_zoom_events(events)
    assert len(pairs) == 1
    assert pairs[0]["in"]["t"] == 3.0
    assert pairs[0]["out"] is None

    # Before zoom
    zoom, _, _, is_zoomed = apply_zoom.calculate_zoom_at_time(1.0, pairs, zoom_factor=2.0, ramp_sec=0.5)
    assert zoom == 1.0
    assert not is_zoomed

    # End of ramp
    zoom, _, _, is_zoomed = apply_zoom.calculate_zoom_at_time(3.5, pairs, zoom_factor=2.0, ramp_sec=0.5)
    assert zoom == pytest.approx(2.0, rel=1e-3)
    assert is_zoomed

    # Long into the future (holding to end of video)
    zoom, _, _, is_zoomed = apply_zoom.calculate_zoom_at_time(60.0, pairs, zoom_factor=2.0, ramp_sec=0.5)
    assert zoom == 2.0
    assert is_zoomed


def test_smoothstep_easing():
    """Verify smoothstep easing behavior at start, midpoint, and hold duration."""
    pairs = [
        {
            "in": {"t": 4.0, "x": 640, "y": 360, "type": "in"},
            "out": {"t": 8.0, "x": 640, "y": 360, "type": "out"},
        }
    ]
    ramp_sec = 0.5
    zoom_factor = 2.0

    z_start, _, _, _ = apply_zoom.calculate_zoom_at_time(4.0, pairs, zoom_factor, ramp_sec)
    assert z_start == pytest.approx(1.0, rel=1e-3)

    z_mid, _, _, _ = apply_zoom.calculate_zoom_at_time(4.25, pairs, zoom_factor, ramp_sec)
    assert z_mid == pytest.approx(1.5, rel=1e-3)

    z_hold, _, _, _ = apply_zoom.calculate_zoom_at_time(6.0, pairs, zoom_factor, ramp_sec)
    assert z_hold == pytest.approx(2.0, rel=1e-3)

    z_out_mid, _, _, _ = apply_zoom.calculate_zoom_at_time(8.25, pairs, zoom_factor, ramp_sec)
    assert z_out_mid == pytest.approx(1.5, rel=1e-3)

    z_end, _, _, _ = apply_zoom.calculate_zoom_at_time(8.5, pairs, zoom_factor, ramp_sec)
    assert z_end == pytest.approx(1.0, rel=1e-3)


def test_rapid_double_tap_continuity():
    """Verify toggling zoom-out before zoom-in ramp finishes produces continuous transition."""
    pairs = [
        {
            "in": {"t": 2.0, "x": 640, "y": 360, "type": "in"},
            "out": {"t": 2.25, "x": 640, "y": 360, "type": "out"},
        }
    ]
    ramp_sec = 0.5
    zoom_factor = 2.0

    z_before, _, _, _ = apply_zoom.calculate_zoom_at_time(2.249, pairs, zoom_factor, ramp_sec)
    z_at_out, _, _, _ = apply_zoom.calculate_zoom_at_time(2.25, pairs, zoom_factor, ramp_sec)
    z_after_out, _, _, _ = apply_zoom.calculate_zoom_at_time(2.75, pairs, zoom_factor, ramp_sec)

    # No discontinuous jump across t_out=2.25
    assert z_before == pytest.approx(z_at_out, abs=0.01)
    assert z_at_out == pytest.approx(1.5, rel=1e-2)
    assert z_after_out == pytest.approx(1.0, rel=1e-3)


def test_zero_ramp_sec_no_division_by_zero():
    """Verify ramp_sec=0 works cleanly without ZeroDivisionError in filter or math."""
    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "ramp_sec": 0.0,
        "events": [
            {"t": 2.0, "x": 640, "y": 360, "type": "in"},
            {"t": 4.0, "x": 640, "y": 360, "type": "out"},
        ],
    }
    filt = apply_zoom.generate_zoompan_filter(sidecar)
    assert "/0" not in filt
    pairs = apply_zoom.pair_zoom_events(sidecar["events"])
    z, _, _, active = apply_zoom.calculate_zoom_at_time(3.0, pairs, zoom_factor=2.0, ramp_sec=0.0)
    assert active and z == 2.0


def test_filter_includes_cfr_supersampling_and_normalized_coords():
    """Verify filtergraph sets PTS=0, normalizes CFR, 2x supersamples, and clamps proportionally."""
    sidecar = {
        "viewport": {"width": 1280, "height": 720},
        "fps": 25,
        "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}],
    }
    filt = apply_zoom.generate_zoompan_filter(sidecar)
    assert filt.startswith("setpts=PTS-STARTPTS,fps=25,scale=iw*2:ih*2:flags=bilinear,zoompan=")
    assert "0.500000*iw-iw/(2*zoom)" in filt
    assert "s=1280x720:fps=25" in filt


def test_no_events_copy_unchanged_path():
    """Verify empty events list produces copy-unchanged command and copies file."""
    empty_sidecar = {"viewport": {"width": 1280, "height": 720}, "events": []}
    filter_str = apply_zoom.generate_zoompan_filter(empty_sidecar)
    assert filter_str == ""

    cmd = apply_zoom.build_ffmpeg_command("input.webm", "output.webm", empty_sidecar)
    assert "-c" in cmd
    assert "copy" in cmd
    assert "-vf" not in cmd

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "plain.webm")
        dst = os.path.join(tmpdir, "plain.zoomed.webm")
        with open(src, "wb") as f:
            f.write(b"PLAIN_WEBM_BYTES")
        out = apply_zoom.apply_zoom(src, output_path=dst)
        assert out == dst
        with open(dst, "rb") as f:
            assert f.read() == b"PLAIN_WEBM_BYTES"


def test_dry_run_command_output():
    """Verify dry-run returns ffmpeg command with VP9 speedup flags without encoding."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dummy_video = os.path.join(tmpdir, "test.webm")
        with open(dummy_video, "wb") as f:
            f.write(b"RIFFdummy")

        dummy_sidecar = os.path.join(tmpdir, "test.zoom.json")
        with open(dummy_sidecar, "w") as f:
            json.dump({
                "viewport": {"width": 1280, "height": 720},
                "events": [{"t": 1.0, "x": 640, "y": 360, "type": "in"}]
            }, f)

        cmd_str = apply_zoom.apply_zoom(dummy_video, dry_run=True)
        assert "ffmpeg" in cmd_str
        assert "zoompan=" in cmd_str
        assert "libvpx-vp9" in cmd_str
        assert "-cpu-used" in cmd_str
        assert "-row-mt" in cmd_str


def test_capture_f5_and_programmatic_zoom_playwright():
    """Integration test verifying F5 hotkey, window.__zoomAt, colored ring, and cross-origin parity."""
    pw = pytest.importorskip("playwright.sync_api")
    import qscript

    qscript._zoom_events = []
    qscript._current_zoom_state = "out"

    with pw.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        ctx.add_init_script(qscript.INJECT_OVERLAY_SCRIPT)
        ctx.expose_function("__pyToggleZoom", lambda x=None, y=None: qscript.record_zoom_event(x, y))
        page = ctx.new_page()
        qscript._context_start_time = time.time()

        page.route("http://site-a.local/**", lambda r: r.fulfill(
            status=200, content_type="text/html", body="<html><body><h1>Site A</h1></body></html>"
        ))
        page.route("http://site-b.local/**", lambda r: r.fulfill(
            status=200, content_type="text/html", body="<html><body><h1>Site B</h1></body></html>"
        ))

        page.goto("http://site-a.local/")
        page.mouse.move(400, 250)
        page.keyboard.press("F5")
        page.wait_for_timeout(120)

        assert page.locator(".__automation_zoom_ring").count() >= 1

        page.keyboard.press("F5")
        page.wait_for_timeout(120)

        res1 = page.evaluate("window.__zoomAt(900, 500)")
        assert res1["type"] == "in"
        assert res1["x"] == 900 and res1["y"] == 500

        # Cross-origin navigation: toggle parity and fallback coordinates must persist
        page.goto("http://site-b.local/")
        res2 = page.evaluate("window.__zoomAt()")
        assert res2["type"] == "out"
        assert res2["x"] == 900 and res2["y"] == 500

        ctx.close()
        browser.close()

    assert len(qscript._zoom_events) == 4
    assert [e["type"] for e in qscript._zoom_events] == ["in", "out", "in", "out"]
    assert qscript._zoom_events[0]["x"] == 400 and qscript._zoom_events[0]["y"] == 250
