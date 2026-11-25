import argparse
import json
import os
import shutil
import subprocess
import sys


def clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamps a numeric value within [min_val, max_val]."""
    return max(min_val, min(val, max_val))


def smoothstep(p: float) -> float:
    """Smooth cubic Hermite interpolation on [0, 1]."""
    p = clamp(float(p), 0.0, 1.0)
    return p * p * (3.0 - 2.0 * p)


def map_mouse_to_video(
    px: float,
    py: float,
    viewport_w: float,
    viewport_h: float,
    video_w: float,
    video_h: float,
) -> tuple:
    """Maps mouse viewport coordinates to target video pixel coordinates."""
    sx = (video_w / viewport_w) if viewport_w > 0 else 1.0
    sy = (video_h / viewport_h) if viewport_h > 0 else 1.0
    return clamp(px * sx, 0.0, video_w), clamp(py * sy, 0.0, video_h)


def clamp_crop(px: float, py: float, iw: float, ih: float, zoom: float) -> tuple:
    """
    Centers crop around (px, py) for given zoom level and clamps
    the top-left crop coordinates (x, y) within valid video frame bounds.
    """
    zoom = max(1.0, float(zoom))
    cw = iw / zoom
    ch = ih / zoom
    max_x = max(0.0, iw - cw)
    max_y = max(0.0, ih - ch)

    cx = px - cw / 2.0
    cy = py - ch / 2.0

    x = clamp(cx, 0.0, max_x)
    y = clamp(cy, 0.0, max_y)
    return x, y


def probe_video_dimensions(video_path: str, ffmpeg_bin: str = "ffmpeg"):
    """Best-effort detection of input video width and height via ffprobe."""
    if not os.path.isfile(video_path) or os.path.getsize(video_path) < 32:
        return None

    ffmpeg_dir = os.path.dirname(ffmpeg_bin)
    ffprobe_name = "ffprobe.exe" if ffmpeg_bin.lower().endswith(".exe") else "ffprobe"
    ffprobe_bin = os.path.join(ffmpeg_dir, ffprobe_name) if ffmpeg_dir else ffprobe_name

    try:
        res = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                video_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
        if res.returncode == 0 and res.stdout:
            info = json.loads(res.stdout)
            streams = info.get("streams", [])
            if streams:
                w = int(streams[0].get("width", 0))
                h = int(streams[0].get("height", 0))
                if w > 0 and h > 0:
                    return w, h
    except Exception:
        pass
    return None


def parse_sidecar(sidecar_input) -> dict:
    """
    Parses sidecar file or dictionary, validating fields and
    normalizing legacy event structures without explicit 'type'.
    """
    if isinstance(sidecar_input, str):
        if not os.path.exists(sidecar_input):
            raise FileNotFoundError(f"Sidecar file not found: {sidecar_input}")
        with open(sidecar_input, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif isinstance(sidecar_input, dict):
        data = dict(sidecar_input)
    else:
        raise TypeError("Sidecar input must be a filepath string or dictionary")

    viewport = data.get("viewport", {})
    width = max(2, int(viewport.get("width", 1280)))
    height = max(2, int(viewport.get("height", 720)))
    fps = max(1.0, float(data.get("fps", 25.0)))
    zoom_factor = max(1.0, float(data.get("zoom_factor", 2.0)))
    ramp_sec = max(0.0, float(data.get("ramp_sec", 0.5)))

    raw_events = data.get("events", [])
    normalized_events = []

    for i, ev in enumerate(raw_events):
        t = max(0.0, float(ev.get("t", 0.0)))
        x = float(ev.get("x", width / 2.0))
        y = float(ev.get("y", height / 2.0))
        evt_type = ev.get("type")
        if not evt_type:
            evt_type = "in" if (i % 2 == 0) else "out"
        evt_type = str(evt_type).lower().strip()
        if evt_type in ("zoom_in", "enter"):
            evt_type = "in"
        elif evt_type in ("zoom_out", "exit"):
            evt_type = "out"
        normalized_events.append({"t": t, "x": x, "y": y, "type": evt_type})

    normalized_events.sort(key=lambda e: (e["t"], 0 if e["type"] == "in" else 1))

    return {
        "viewport": {"width": width, "height": height},
        "fps": fps,
        "zoom_factor": zoom_factor,
        "ramp_sec": ramp_sec,
        "events": normalized_events,
    }


def pair_zoom_events(events: list) -> list:
    """
    Pairs each 'in' event with the next 'out' event.
    If an 'in' event has no following 'out', it forms an open-ended pair (out=None).
    """
    pairs = []
    current_in = None

    for ev in events:
        evt_type = ev.get("type")
        if evt_type == "in":
            if current_in is not None:
                pairs.append({"in": current_in, "out": None})
            current_in = ev
        elif evt_type == "out":
            if current_in is not None:
                pairs.append({"in": current_in, "out": ev})
                current_in = None

    if current_in is not None:
        pairs.append({"in": current_in, "out": None})

    return pairs


def calculate_ramp_frames(ramp_sec: float, fps: float) -> int:
    """Calculates number of frames in the ramp transition duration."""
    return max(1, int(round(max(0.0, ramp_sec) * fps)))


def calculate_zoom_at_time(t: float, pairs: list, zoom_factor: float = 2.0, ramp_sec: float = 0.5) -> tuple:
    """
    Calculates zoom level, target focus coordinates, and active status at timestamp t.
    Uses smoothstep easing: 3*p^2 - 2*p^3.
    Supports rapid double-taps (hold < ramp_sec), open-ended zooms, and multiple pairs.
    """
    t = float(t)
    zoom_factor = max(1.0, float(zoom_factor))
    ramp_sec = max(0.0, float(ramp_sec))

    for idx, pair in enumerate(pairs):
        in_ev = pair["in"]
        out_ev = pair["out"]
        t_in = in_ev["t"]
        px = in_ev["x"]
        py = in_ev["y"]
        next_t_in = pairs[idx + 1]["in"]["t"] if (idx + 1 < len(pairs)) else float("inf")

        if t < t_in or t >= next_t_in:
            continue

        if out_ev is not None:
            t_out = max(t_in, out_ev["t"])
            if ramp_sec <= 0.0:
                if t < t_out:
                    return zoom_factor, px, py, True
                continue

            t_ramp_in_full = t_in + ramp_sec
            t_ramp_out = min(t_out + ramp_sec, next_t_in)
            if t >= t_ramp_out:
                continue

            if t_out >= t_ramp_in_full:
                if t < t_ramp_in_full:
                    ease = smoothstep((t - t_in) / ramp_sec)
                    return 1.0 + (zoom_factor - 1.0) * ease, px, py, True
                if t < t_out:
                    return zoom_factor, px, py, True
                ease = smoothstep((t - t_out) / ramp_sec)
                return zoom_factor - (zoom_factor - 1.0) * ease, px, py, True
            else:
                # Rapid double-tap: zoom out smoothly from whatever zoom level was reached at t_out
                z_peak = 1.0 + (zoom_factor - 1.0) * smoothstep((t_out - t_in) / ramp_sec)
                if t < t_out:
                    ease = smoothstep((t - t_in) / ramp_sec)
                    return 1.0 + (zoom_factor - 1.0) * ease, px, py, True
                ease = smoothstep((t - t_out) / ramp_sec)
                return z_peak - (z_peak - 1.0) * ease, px, py, True
        else:
            if ramp_sec <= 0.0:
                return zoom_factor, px, py, True
            t_ramp_in = t_in + ramp_sec
            if t < t_ramp_in:
                ease = smoothstep((t - t_in) / ramp_sec)
                return 1.0 + (zoom_factor - 1.0) * ease, px, py, True
            return zoom_factor, px, py, True

    return 1.0, 0.0, 0.0, False


def generate_zoompan_filter(
    sidecar_data: dict,
    output_width: int = None,
    output_height: int = None,
) -> str:
    """
    Constructs the ffmpeg filtergraph (CFR normalization + 2x supersampling + zoompan)
    with smoothstep easing, viewport-to-video ratio mapping, and frame clamping.
    """
    viewport = sidecar_data.get("viewport", {})
    vw = max(2, int(viewport.get("width", 1280)))
    vh = max(2, int(viewport.get("height", 720)))
    out_w = max(2, int(output_width or vw))
    out_h = max(2, int(output_height or vh))
    fps = max(1, int(round(sidecar_data.get("fps", 25.0))))
    zoom_factor = max(1.0, float(sidecar_data.get("zoom_factor", 2.0)))
    ramp_sec = max(0.0, float(sidecar_data.get("ramp_sec", 0.5)))

    pairs = pair_zoom_events(sidecar_data.get("events", []))
    if not pairs:
        return ""

    z_expr = "1.0"
    x_expr = "0"
    y_expr = "0"

    for idx in range(len(pairs) - 1, -1, -1):
        pair = pairs[idx]
        in_ev = pair["in"]
        out_ev = pair["out"]
        t_in = in_ev["t"]
        px = in_ev["x"]
        py = in_ev["y"]
        next_t_in = pairs[idx + 1]["in"]["t"] if (idx + 1 < len(pairs)) else None

        px_norm = clamp(px / float(vw), 0.0, 1.0)
        py_norm = clamp(py / float(vh), 0.0, 1.0)
        pair_x_str = f"clip({px_norm:.6f}*iw-iw/(2*zoom),0,iw-iw/zoom)"
        pair_y_str = f"clip({py_norm:.6f}*ih-ih/(2*zoom),0,ih-ih/zoom)"

        if out_ev is not None:
            t_out = max(t_in, out_ev["t"])
            if ramp_sec <= 0.0:
                upper = min(t_out, next_t_in) if next_t_in is not None else t_out
                pair_z_str = f"{zoom_factor}"
                interval_cond = f"between(it,{t_in},{upper})"
            else:
                t_ramp_in_full = t_in + ramp_sec
                t_ramp_out = t_out + ramp_sec
                upper = min(t_ramp_out, next_t_in) if next_t_in is not None else t_ramp_out

                in_p = f"((it-{t_in})/{ramp_sec})"
                in_ease = f"({in_p}*{in_p}*(3-2*{in_p}))"
                out_p = f"((it-{t_out})/{ramp_sec})"
                out_ease = f"({out_p}*{out_p}*(3-2*{out_p}))"

                if t_out >= t_ramp_in_full:
                    pair_z_str = (
                        f"if(between(it,{t_in},{t_ramp_in_full}),"
                        f"1.0+({zoom_factor}-1.0)*{in_ease},"
                        f"if(between(it,{t_ramp_in_full},{t_out}),"
                        f"{zoom_factor},"
                        f"if(between(it,{t_out},{t_ramp_out}),"
                        f"{zoom_factor}-({zoom_factor}-1.0)*{out_ease},"
                        f"1.0)))"
                    )
                else:
                    z_peak = 1.0 + (zoom_factor - 1.0) * smoothstep((t_out - t_in) / ramp_sec)
                    pair_z_str = (
                        f"if(between(it,{t_in},{t_out}),"
                        f"1.0+({zoom_factor}-1.0)*{in_ease},"
                        f"if(between(it,{t_out},{t_ramp_out}),"
                        f"{z_peak:.6f}-({z_peak:.6f}-1.0)*{out_ease},"
                        f"1.0))"
                    )
                interval_cond = f"between(it,{t_in},{upper})"
        else:
            if ramp_sec <= 0.0:
                pair_z_str = f"{zoom_factor}"
            else:
                t_ramp_in = t_in + ramp_sec
                in_p = f"((it-{t_in})/{ramp_sec})"
                in_ease = f"({in_p}*{in_p}*(3-2*{in_p}))"
                pair_z_str = (
                    f"if(between(it,{t_in},{t_ramp_in}),"
                    f"1.0+({zoom_factor}-1.0)*{in_ease},"
                    f"{zoom_factor})"
                )

            if next_t_in is not None:
                interval_cond = f"between(it,{t_in},{next_t_in})"
            else:
                interval_cond = f"gte(it,{t_in})"

        z_expr = f"if({interval_cond},{pair_z_str},{z_expr})"
        x_expr = f"if({interval_cond},{pair_x_str},{x_expr})"
        y_expr = f"if({interval_cond},{pair_y_str},{y_expr})"

    return (
        f"setpts=PTS-STARTPTS,"
        f"fps={fps},"
        f"scale=iw*2:ih*2:flags=bilinear,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={out_w}x{out_h}:fps={fps}"
    )


def find_sidecar_file(video_path: str, explicit_sidecar: str = None):
    """Locates sidecar JSON file associated with the video."""
    if explicit_sidecar and os.path.exists(explicit_sidecar):
        return explicit_sidecar

    base_no_ext = os.path.splitext(video_path)[0]
    candidates = [
        f"{base_no_ext}.zoom.json",
        f"{video_path}.zoom.json",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def build_ffmpeg_command(
    video_path: str,
    output_path: str,
    sidecar_data: dict = None,
    ffmpeg_bin: str = "ffmpeg",
    output_width: int = None,
    output_height: int = None,
) -> list:
    """Builds the ffmpeg argument list for rendering zoom or copying unchanged."""
    filter_str = ""
    if sidecar_data and sidecar_data.get("events"):
        filter_str = generate_zoompan_filter(
            sidecar_data,
            output_width=output_width,
            output_height=output_height,
        )

    if not filter_str:
        return [
            ffmpeg_bin,
            "-y",
            "-i",
            video_path,
            "-c",
            "copy",
            output_path,
        ]

    return [
        ffmpeg_bin,
        "-y",
        "-i",
        video_path,
        "-vf",
        filter_str,
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "0",
        "-crf",
        "30",
        "-deadline",
        "good",
        "-cpu-used",
        "4",
        "-row-mt",
        "1",
        "-pix_fmt",
        "yuv420p",
        output_path,
    ]


def apply_zoom(
    video_path: str,
    sidecar_path: str = None,
    output_path: str = None,
    dry_run: bool = False,
    ffmpeg_bin: str = "ffmpeg",
) -> str:
    """
    Applies Camtasia-style zoom effect to video based on sidecar metadata.
    If no events exist, the video is preserved unchanged.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Input video not found: {video_path}")

    if not output_path:
        base_no_ext = os.path.splitext(video_path)[0]
        output_path = f"{base_no_ext}.zoomed.webm"

    actual_sidecar = find_sidecar_file(video_path, sidecar_path)
    sidecar_data = None

    if actual_sidecar:
        sidecar_data = parse_sidecar(actual_sidecar)

    probed_dims = probe_video_dimensions(video_path, ffmpeg_bin=ffmpeg_bin)
    out_w, out_h = probed_dims if probed_dims else (None, None)

    cmd = build_ffmpeg_command(
        video_path=video_path,
        output_path=output_path,
        sidecar_data=sidecar_data,
        ffmpeg_bin=ffmpeg_bin,
        output_width=out_w,
        output_height=out_h,
    )

    cmd_str = subprocess.list2cmdline(cmd)

    if dry_run:
        print(cmd_str)
        return cmd_str

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if not sidecar_data or not sidecar_data.get("events"):
        if os.path.abspath(video_path) != os.path.abspath(output_path):
            shutil.copyfile(video_path, output_path)
        return output_path

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg execution failed (code {result.returncode}): {result.stderr}")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Apply Camtasia-style zoom effect to recorded webm videos using ffmpeg."
    )
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Path to input .webm video.",
    )
    parser.add_argument(
        "positional_video",
        nargs="?",
        default=None,
        help="Optional positional path to input .webm video.",
    )
    parser.add_argument(
        "--sidecar",
        type=str,
        default=None,
        help="Path to .zoom.json sidecar file (default: auto-detect <video>.zoom.json).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path for zoomed output .webm (default: <video>.zoomed.webm).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print ffmpeg command without encoding.",
    )
    parser.add_argument(
        "--ffmpeg",
        type=str,
        default="ffmpeg",
        help="Path to ffmpeg executable (default: 'ffmpeg').",
    )

    args = parser.parse_args()
    video_path = args.video or args.positional_video

    if not video_path:
        parser.print_help()
        sys.exit(1)

    try:
        apply_zoom(
            video_path=video_path,
            sidecar_path=args.sidecar,
            output_path=args.output,
            dry_run=args.dry_run,
            ffmpeg_bin=args.ffmpeg,
        )
    except Exception as err:
        sys.stderr.write(f"Error: {err}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
