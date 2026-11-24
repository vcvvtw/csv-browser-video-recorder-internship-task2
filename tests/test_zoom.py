import json
import os
import tempfile
import pytest

import apply_zoom


def test_ramp_frame_calculation():
    """Verify ramp duration to frame calculation across common frame rates."""
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.5, fps=25.0) in (12, 13)
    assert apply_zoom.calculate_ramp_frames(ramp_sec=1.0, fps=30.0) == 30
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.2, fps=25.0) == 5
    assert apply_zoom.calculate_ramp_frames(ramp_sec=0.0, fps=25.0) == 1


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

    # Start of ramp in
    z_start, _, _, _ = apply_zoom.calculate_zoom_at_time(4.0, pairs, zoom_factor, ramp_sec)
    assert z_start == pytest.approx(1.0, rel=1e-3)

    # Halfway through ramp in (p = 0.5 -> ease = 0.5 -> z = 1.5)
    z_mid, _, _, _ = apply_zoom.calculate_zoom_at_time(4.25, pairs, zoom_factor, ramp_sec)
    assert z_mid == pytest.approx(1.5, rel=1e-3)

    # Peak hold
    z_hold, _, _, _ = apply_zoom.calculate_zoom_at_time(6.0, pairs, zoom_factor, ramp_sec)
    assert z_hold == pytest.approx(2.0, rel=1e-3)

    # Ramp out midpoint
    z_out_mid, _, _, _ = apply_zoom.calculate_zoom_at_time(8.25, pairs, zoom_factor, ramp_sec)
    assert z_out_mid == pytest.approx(1.5, rel=1e-3)

    # End of ramp out
    z_end, _, _, _ = apply_zoom.calculate_zoom_at_time(8.5, pairs, zoom_factor, ramp_sec)
    assert z_end == pytest.approx(1.0, rel=1e-3)


def test_no_events_copy_unchanged_path():
    """Verify empty events list produces copy-unchanged command."""
    empty_sidecar = {"viewport": {"width": 1280, "height": 720}, "events": []}
    filter_str = apply_zoom.generate_zoompan_filter(empty_sidecar)
    assert filter_str == ""

    cmd = apply_zoom.build_ffmpeg_command("input.webm", "output.webm", empty_sidecar)
    assert "-c" in cmd
    assert "copy" in cmd
    assert "-vf" not in cmd


def test_dry_run_command_output():
    """Verify dry-run returns ffmpeg command without encoding."""
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
