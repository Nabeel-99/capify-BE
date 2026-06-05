import shutil
from fastapi import FastAPI, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
import subprocess
import tempfile
import os
import uuid
import json
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG  = "/usr/local/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

FONT_NAME_MAP = {
    "Bangers":    "Bangers",
    "Bebas Neue": "Bebas Neue",
    "BebasNeue":  "Bebas Neue",
    "Montserrat": "Montserrat",
    "Poppins":    "Poppins",
}

FONT_FILE_MAP = {
    "Bangers":    "Bangers-Regular",
    "Bebas Neue": "BebasNeue",
    "BebasNeue":  "BebasNeue",
    "Montserrat": "Montserrat",
    "Poppins":    "Poppins",
}

def resolve_font_name(font: str) -> str:
    return FONT_NAME_MAP.get(font, font)

def ensure_canonical_font(font: str):
    font_name = resolve_font_name(font)
    font_file = FONT_FILE_MAP.get(font, font.replace(" ", ""))
    src = os.path.join(FONTS_DIR, f"{font_file}.ttf")
    dst = os.path.join(FONTS_DIR, f"{font_name.replace(' ', '')}.ttf")
    if not os.path.exists(dst) and os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"copied {src} -> {dst}")

def get_duration(video_path: str) -> float:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def hex_to_ass_color(hex_color: str) -> str:
    """#RRGGBB -> &H00BBGGRR"""
    h = hex_color.lstrip("#")
    if len(h) == 8:
        h = h[:6]
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()

def seconds_to_ass(s: float) -> str:
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = s % 60
    return f"{h}:{m:02d}:{sc:05.2f}"

def clean_word(text: str) -> str:
    return text.replace("{", "").replace("}", "").upper()

def ass_header(font, font_size, alignment, primary, secondary,
               play_res_x=1920, play_res_y=1080, margin_v=None) -> str:
    outline  = "&H00000000"
    back     = "&H00000000"
    mv       = margin_v if margin_v is not None else 60
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{primary},{secondary},{outline},{back},1,0,0,0,100,100,0,0,1,4,0,{alignment},60,60,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass_karaoke(subtitles, font, font_size, alignment,
                      text_color, highlight_color,
                      play_res_x=1920, play_res_y=1080, margin_v=None) -> str:
    white     = hex_to_ass_color(text_color)
    highlight = hex_to_ass_color(highlight_color)
    header    = ass_header(font, font_size, alignment, white, white,
                           play_res_x, play_res_y, margin_v)
    white_bgr     = white[4:]
    highlight_bgr = highlight[4:]
    lines = []
    for chunk in subtitles:
        words     = chunk.get("words", [])
        chunk_end = chunk["end"]
        if not words:
            start = seconds_to_ass(chunk["start"])
            end   = seconds_to_ass(chunk["end"])
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{chunk['text'].upper()}")
            continue
        for active_i, active_word in enumerate(words):
            ev_start = active_word["start"]
            ev_end   = words[active_i + 1]["start"] if active_i + 1 < len(words) else chunk_end
            parts = []
            for j, word in enumerate(words):
                w = clean_word(word["text"])
                if j == active_i:
                    parts.append(f"{{\\c&H{highlight_bgr}&}}{w}{{\\c&H{white_bgr}&}}")
                else:
                    parts.append(w)
            lines.append(
                f"Dialogue: 0,{seconds_to_ass(ev_start)},{seconds_to_ass(ev_end)},"
                f"Default,,0,0,0,," + " ".join(parts)
            )
    return header + "\n".join(lines)


def build_ass_fade(subtitles, font, font_size, alignment,
                   text_color, highlight_color,
                   play_res_x=1920, play_res_y=1080, margin_v=None) -> str:
    primary = hex_to_ass_color(text_color)
    header  = ass_header(font, font_size, alignment, primary, primary,
                         play_res_x, play_res_y, margin_v)
    lines = []
    for chunk in subtitles:
        start = seconds_to_ass(chunk["start"])
        end   = seconds_to_ass(chunk["end"])
        text  = chunk["text"].upper()
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\fad(200,200)}}{text}")
    return header + "\n".join(lines)


def build_ass_typewriter(subtitles, font, font_size, alignment,
                          text_color, highlight_color,
                          play_res_x=1920, play_res_y=1080, margin_v=None) -> str:
    primary = hex_to_ass_color(text_color)
    header  = ass_header(font, font_size, alignment, primary, primary,
                         play_res_x, play_res_y, margin_v)
    lines = []
    for chunk in subtitles:
        chunk_end = seconds_to_ass(chunk["end"])
        words     = chunk.get("words", [])
        if not words:
            start = seconds_to_ass(chunk["start"])
            lines.append(f"Dialogue: 0,{start},{chunk_end},Default,,0,0,0,,{chunk['text'].upper()}")
            continue
        revealed = []
        for word in words:
            revealed.append(clean_word(word["text"]))
            word_start = seconds_to_ass(word["start"])
            lines.append(f"Dialogue: 0,{word_start},{chunk_end},Default,,0,0,0,," + " ".join(revealed))
    return header + "\n".join(lines)

@app.post("/assemble")
async def assemble_video(request: Request):
    form    = await request.form()
    audio   = form["audio"]
    timings = form["timings"]
    music   = form.get("music")

    job_id  = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp()

    print(f"\n{'='*50}")
    print(f"[ASSEMBLE] job_id={job_id}")
    print(f"[ASSEMBLE] tmp_dir={tmp_dir}")
    print(f"[ASSEMBLE] audio filename={audio.filename}")
    print(f"[ASSEMBLE] music={'yes: '+music.filename if music else 'none'}")

    audio_path = os.path.join(tmp_dir, f"audio_{job_id}.mp3")
    music_path = os.path.join(tmp_dir, f"music_{job_id}.mp3") if music else None
    out_16     = os.path.join(tmp_dir, f"out16_{job_id}.mp4")
    out_9      = os.path.join(tmp_dir, f"out9_{job_id}.mp4")

    with open(audio_path, "wb") as f:
        f.write(await audio.read())
    print(f"[ASSEMBLE] audio saved: {os.path.getsize(audio_path)} bytes")

    if music and music_path:
        with open(music_path, "wb") as f:
            f.write(await music.read())
        print(f"[ASSEMBLE] music saved: {os.path.getsize(music_path)} bytes")

    scenes = json.loads(timings)
    print(f"[ASSEMBLE] scenes count={len(scenes)}")
    for i, s in enumerate(scenes):
        print(f"  scene[{i}]: start={s['start']} end={s['end']} hasImage={s['hasImage']}")

    img_keys = [k for k in form if k.startswith("image_")]
    print(f"[ASSEMBLE] image keys received: {img_keys}")
    for key in img_keys:
        idx      = int(key.split("_")[1])
        img_file = form[key]
        img_path = os.path.join(tmp_dir, f"img_{idx}_{job_id}.jpg")
        with open(img_path, "wb") as f:
            f.write(await img_file.read())
        print(f"[ASSEMBLE] saved image_{idx}: {os.path.getsize(img_path)} bytes -> {img_path}")

    async def generate():
        TRANS    = 2.0
        FPS      = 25
        ZOOM_END = 1.12

        scene_img_paths = {}
        for i, scene in enumerate(scenes):
            img_path = os.path.join(tmp_dir, f"img_{i}_{job_id}.jpg")
            if os.path.exists(img_path):
                scene_img_paths[i] = img_path
                print(f"[GEN] scene {i}: image found")
            else:
                print(f"[GEN] scene {i}: NO image — will use black frame")

        segment_paths = []
        durations     = []
        total         = len(scenes)

        for i, scene in enumerate(scenes):
            if len(scenes) == 1:
                # single scene — just use its own duration
                dur = max(TRANS + 0.5, scene["end"] - scene["start"])
            elif i == 0:
                # first scene: run until next scene's audio start (no +TRANS here)
                dur = max(TRANS + 0.5, scenes[i + 1]["start"] - scene["start"])
            elif i < len(scenes) - 1:
                # middle scenes: add TRANS to compensate for xfade consumption
                dur = max(TRANS + 0.5, scenes[i + 1]["start"] - scene["start"] + TRANS)
            else:
                # last scene: its own duration
                dur = max(TRANS + 0.5, scene["end"] - scene["start"])

            durations.append(dur)
            seg_path = os.path.join(tmp_dir, f"seg_{i}_{job_id}.mp4")
            segment_paths.append(seg_path)
            img_p = scene_img_paths.get(i)

            print(f"[GEN] building segment {i}: dur={dur:.2f}s img={'yes' if img_p else 'black'}")

            if img_p:
                frames    = max(int(dur * FPS), 1)
                increment = (ZOOM_END - 1.0) / frames

                vf = (
                    f"scale=7680:4320,"
                    f"zoompan="
                    f"z='1+{increment:.8f}*on':"
                    f"x='trunc(iw/2-(iw/zoom/2))':"
                    f"y='trunc(ih/2-(ih/zoom/2))':"
                    f"d={frames}:s=1920x1080:fps={FPS}"
                )
                seg_cmd = [
                    FFMPEG,
                    "-framerate", str(FPS),
                    "-loop", "1", "-i", img_p,
                    "-t", str(dur),
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-an", "-y", seg_path
                ]
            else:
                seg_cmd = [
                    FFMPEG,
                    "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:d={dur}:r={FPS}",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-an", "-y", seg_path
                ]

            result = subprocess.run(seg_cmd, capture_output=True)
            if result.returncode != 0:
                print(f"[GEN] ERROR on segment {i}: {result.stderr.decode()}")
            else:
                print(f"[GEN] segment {i} OK: {os.path.getsize(seg_path)} bytes")

            yield f"data: {json.dumps({'progress': int((i / total) * 55)})}\n\n"

        # ── Cross-dissolve concat ─────────────────────────────────────────────
        raw_video = os.path.join(tmp_dir, f"raw_video_{job_id}.mp4")

        if len(segment_paths) == 1:
            os.rename(segment_paths[0], raw_video)
            print(f"[GEN] single segment, skipping concat")
        else:
            # Use TRANS directly — safe_trans caused drift when it != TRANS
            ffmpeg_inputs = []
            for sp in segment_paths:
                ffmpeg_inputs += ["-i", sp]

            filter_parts = []
            offset = max(0.05, durations[0] - TRANS)
            filter_parts.append(
                f"[0][1]xfade=transition=fade:duration={TRANS:.2f}:offset={offset:.3f}[v01]"
            )
            prev_label = "v01"
            cumulative = durations[0] + durations[1] - TRANS

            for i in range(2, len(segment_paths)):
                offset    = max(0.05, cumulative - TRANS)
                new_label = f"v{i:02d}"
                filter_parts.append(
                    f"[{prev_label}][{i}]"
                    f"xfade=transition=fade:duration={TRANS:.2f}:offset={offset:.3f}"
                    f"[{new_label}]"
                )
                prev_label  = new_label
                cumulative += durations[i] - TRANS

            # ← THIS BLOCK WAS MISSING — actually runs the xfade
            result = subprocess.run([
                FFMPEG, *ffmpeg_inputs,
                "-filter_complex", ";".join(filter_parts),
                "-map", f"[{prev_label}]",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-y", raw_video
            ], capture_output=True)

            if result.returncode != 0:
                print(f"[GEN] xfade ERROR: {result.stderr.decode()}")
            else:
                print(f"[GEN] xfade concat OK: {os.path.getsize(raw_video)} bytes")
        # ── Extend video to match audio (hold last frame) ─────────────────────
        audio_dur = get_duration(audio_path)
        video_dur = get_duration(raw_video)
        print(f"[GEN] audio={audio_dur:.2f}s  video={video_dur:.2f}s")

        if audio_dur > video_dur + 0.1:
            pad      = audio_dur - video_dur + 0.2
            extended = os.path.join(tmp_dir, f"extended_{job_id}.mp4")
            result   = subprocess.run([
                FFMPEG, "-i", raw_video,
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-y", extended
            ], capture_output=True)
            if result.returncode == 0:
                raw_video = extended
                print(f"[GEN] extended video to {audio_dur + 0.2:.2f}s ✓")
            else:
                print(f"[GEN] extend ERROR: {result.stderr.decode()}")

        yield f"data: {json.dumps({'progress': 70})}\n\n"

        # ── Mux video + audio ─────────────────────────────────────────────────
        if music_path:
            mux_cmd = [
                FFMPEG,
                "-i", raw_video, "-i", audio_path, "-i", music_path,
                "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:weights=1 0.25[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-shortest", "-y", out_16
            ]
        else:
            mux_cmd = [
                FFMPEG, "-i", raw_video, "-i", audio_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-shortest", "-y", out_16
            ]

        result = subprocess.run(mux_cmd, capture_output=True)
        if result.returncode != 0:
            print(f"[GEN] mux ERROR: {result.stderr.decode()}")
        else:
            print(f"[GEN] mux OK: {os.path.getsize(out_16)} bytes -> {out_16}")

        yield f"data: {json.dumps({'progress': 85})}\n\n"

        # ── 9:16 crop ─────────────────────────────────────────────────────────
        result = subprocess.run([
            FFMPEG, "-i", out_16,
            "-filter_complex",
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];"
            "[0:v]scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[out]",
            "-map", "[out]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "copy", "-y", out_9
        ], capture_output=True)
        if result.returncode != 0:
            print(f"[GEN] crop ERROR: {result.stderr.decode()}")
        else:
            print(f"[GEN] 9:16 OK: {os.path.getsize(out_9)} bytes -> {out_9}")

        yield f"data: {json.dumps({'progress': 95})}\n\n"

        print(f"[GEN] DONE. file16={out_16}  file9={out_9}")
        yield f"data: {json.dumps({'progress': 100, 'done': True, 'file16': out_16, 'file9': out_9})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/export")
async def export_video(
    video:           UploadFile = File(...),
    subtitles_json:  str        = Form(...),
    font:            str        = Form("Bangers"),
    font_size:       int        = Form(32),
    position:        str        = Form("bottom"),
    text_color:      str        = Form("#ffffff"),
    highlight_color: str        = Form("#a78bfa"),
    animation:       str        = Form("karaoke"),
    aspect_ratio:    str        = Form("16:9"),
    trim_start:      float      = Form(-1.0),
    trim_end:        float      = Form(-1.0),
):
    job_id  = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp()

    video_path   = os.path.join(tmp_dir, f"input_{job_id}.mp4")
    cropped_path = os.path.join(tmp_dir, f"cropped_{job_id}.mp4")
    ass_path     = os.path.join(tmp_dir, f"subs_{job_id}.ass")
    output_path  = os.path.join(tmp_dir, f"output_{job_id}.mp4")

    with open(video_path, "wb") as f:
        f.write(await video.read())

    do_trim = trim_start >= 0 and trim_end > trim_start

    subtitles = json.loads(subtitles_json)
    if do_trim:
        offset = trim_start
        subtitles = [
            {**s, "start": s["start"] - offset, "end": s["end"] - offset,
             "words": [{**w, "start": w["start"] - offset, "end": w["end"] - offset}
                       for w in s.get("words", [])]}
            for s in subtitles
            if s["end"] > offset and s["start"] < trim_end
        ]

    font_name        = resolve_font_name(font)
    scaled_font_size = int(font_size * 2.2)
    ensure_canonical_font(font)
    
    if do_trim:
        trimmed_path = os.path.join(tmp_dir, f"trimmed_{job_id}.mp4")
        trim_result = subprocess.run([
            FFMPEG,
            "-ss", str(trim_start),
            "-i", video_path,
            "-t", str(trim_end - trim_start),
            "-c", "copy",
            "-y", trimmed_path
        ], capture_output=True)
        if trim_result.returncode != 0:
            async def trim_err():
                yield f"data: {json.dumps({'error': trim_result.stderr.decode()})}\n\n"
            return StreamingResponse(trim_err(), media_type="text/event-stream")
        video_path = trimmed_path

    is_portrait = aspect_ratio == "9:16"
    alignment   = 8 if position == "top" else 5 if position == "center" else 2

    if is_portrait:
        play_res_x, play_res_y = 1080, 1920
        portrait_margin_v = 350
    else:
        play_res_x, play_res_y = 1920, 1080
        portrait_margin_v = None

    if animation == "fade":
        ass_content = build_ass_fade(subtitles, font_name, scaled_font_size,
                                     alignment, text_color, highlight_color,
                                     play_res_x, play_res_y, portrait_margin_v)
    elif animation == "typewriter":
        ass_content = build_ass_typewriter(subtitles, font_name, scaled_font_size,
                                           alignment, text_color, highlight_color,
                                           play_res_x, play_res_y, portrait_margin_v)
    else:
        ass_content = build_ass_karaoke(subtitles, font_name, scaled_font_size,
                                        alignment, text_color, highlight_color,
                                        play_res_x, play_res_y, portrait_margin_v)

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    safe_ass   = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    safe_fonts = FONTS_DIR.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf_filter  = f"ass='{safe_ass}':fontsdir='{safe_fonts}'"

    async def generate():
        if is_portrait:
            crop_dur = get_duration(video_path)
            crop_cmd = [
                FFMPEG, "-i", video_path,
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];"
                "[0:v]scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[out]",
                "-map", "[out]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "copy",
                "-progress", "pipe:1", "-nostats",
                "-y", cropped_path
            ]
            crop_proc = subprocess.Popen(crop_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time_pat_crop = re.compile(r"out_time_ms=(\d+)")
            for line in crop_proc.stdout:
                m = time_pat_crop.search(line)
                if m and crop_dur > 0:
                    t_s = int(m.group(1)) / 1_000_000
                    progress = min(int((t_s / crop_dur) * 45), 45)
                    yield f"data: {json.dumps({'progress': progress})}\n\n"
            crop_proc.wait()
            if crop_proc.returncode != 0:
                err = crop_proc.stderr.read()
                yield f"data: {json.dumps({'error': err})}\n\n"
                return

        burn_input = cropped_path if is_portrait else video_path
        duration   = get_duration(burn_input)

        print(f"animation={animation}  font={font_name}  size={scaled_font_size}  portrait={is_portrait}")

        cmd = [
            FFMPEG, "-i", burn_input,
            "-vf", vf_filter,
            "-c:a", "copy", "-c:v", "libx264", "-preset", "ultrafast",
            "-progress", "pipe:1", "-nostats",
            "-y", output_path,
        ]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time_pat = re.compile(r"out_time_ms=(\d+)")
        base = 45 if is_portrait else 0
        for line in process.stdout:
            m = time_pat.search(line)
            if m and duration > 0:
                t_s      = int(m.group(1)) / 1_000_000
                progress = base + min(int((t_s / duration) * (95 - base)), 95 - base)
                yield f"data: {json.dumps({'progress': progress})}\n\n"
        process.wait()
        if process.returncode == 0:
            yield f"data: {json.dumps({'progress': 100, 'done': True, 'file': output_path})}\n\n"
        else:
            err = process.stderr.read()
            print("FFmpeg error:", err)
            yield f"data: {json.dumps({'error': err})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/trim")
async def trim_video(
    video: UploadFile = File(...),
    start: float = Form(...),
    end:   float = Form(...),
):
    job_id  = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp()
    video_path  = os.path.join(tmp_dir, f"input_{job_id}.mp4")
    output_path = os.path.join(tmp_dir, f"trimmed_{job_id}.mp4")

    with open(video_path, "wb") as f:
        f.write(await video.read())

    duration = end - start
    result = subprocess.run([
        FFMPEG,
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c", "copy",
        "-y", output_path
    ], capture_output=True)

    if result.returncode != 0:
        return JSONResponse(status_code=500, content={"error": result.stderr.decode()})

    return FileResponse(output_path, media_type="video/mp4", filename="capify-trim.mp4")

@app.get("/download")
def download_file(path: str = Query(...)):
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return FileResponse(path, media_type="video/mp4", filename="capify-export.mp4")

@app.get("/health")
def health():
    return {"status": "ok"}