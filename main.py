import shutil
from fastapi import FastAPI, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
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

def ass_header(font, font_size, alignment, primary, secondary) -> str:
    outline = "&H00000000"
    back    = "&H00000000"
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{primary},{secondary},{outline},{back},1,0,0,0,100,100,0,0,1,4,0,{alignment},60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def build_ass_karaoke(subtitles, font, font_size, alignment,
                      text_color, highlight_color) -> str:
    """
    One Dialogue line per word-interval. Each line shows the full chunk but
    uses an inline \\c colour tag to highlight exactly one word — the active one.
    This mirrors the canvas exactly: only the word whose start<=t<next_start is lit.
    No \k tricks, no timing ambiguity.
    """
    white     = hex_to_ass_color(text_color)
    highlight = hex_to_ass_color(highlight_color)
    header    = ass_header(font, font_size, alignment, white, white)

    # strip &H00 prefix to get BBGGRR for inline \c tags
    white_bgr     = white[4:]      # e.g. "FFFFFF"
    highlight_bgr = highlight[4:]  # e.g. "FA8BA7"

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
                    # highlight this word, reset colour after it
                    parts.append(f"{{\\c&H{highlight_bgr}&}}{w}{{\\c&H{white_bgr}&}}")
                else:
                    parts.append(w)

            lines.append(
                f"Dialogue: 0,{seconds_to_ass(ev_start)},{seconds_to_ass(ev_end)},"
                f"Default,,0,0,0,," + " ".join(parts)
            )

    return header + "\n".join(lines)


def build_ass_fade(subtitles, font, font_size, alignment,
                   text_color, highlight_color) -> str:
    primary = hex_to_ass_color(text_color)
    header  = ass_header(font, font_size, alignment, primary, primary)
    lines = []
    for chunk in subtitles:
        start = seconds_to_ass(chunk["start"])
        end   = seconds_to_ass(chunk["end"])
        text  = chunk["text"].upper()
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{{\\fad(200,200)}}{text}")
    return header + "\n".join(lines)


def build_ass_typewriter(subtitles, font, font_size, alignment,
                          text_color, highlight_color) -> str:
    primary = hex_to_ass_color(text_color)
    header  = ass_header(font, font_size, alignment, primary, primary)
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
):
    job_id  = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp()

    video_path  = os.path.join(tmp_dir, f"input_{job_id}.mp4")
    ass_path    = os.path.join(tmp_dir, f"subs_{job_id}.ass")
    output_path = os.path.join(tmp_dir, f"output_{job_id}.mp4")

    with open(video_path, "wb") as f:
        f.write(await video.read())

    subtitles        = json.loads(subtitles_json)
    alignment        = 8 if position == "top" else 5 if position == "center" else 2
    font_name        = resolve_font_name(font)
    scaled_font_size = int(font_size * 2.2)

    ensure_canonical_font(font)

    if animation == "fade":
        ass_content = build_ass_fade(subtitles, font_name, scaled_font_size,
                                     alignment, text_color, highlight_color)
    elif animation == "typewriter":
        ass_content = build_ass_typewriter(subtitles, font_name, scaled_font_size,
                                           alignment, text_color, highlight_color)
    else:
        ass_content = build_ass_karaoke(subtitles, font_name, scaled_font_size,
                                        alignment, text_color, highlight_color)

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    safe_ass   = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    safe_fonts = FONTS_DIR.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf_filter  = f"ass='{safe_ass}':fontsdir='{safe_fonts}'"
    duration   = get_duration(video_path)

    print(f"animation={animation}  font={font_name}  size={scaled_font_size}")

    cmd = [
        FFMPEG,
        "-i", video_path,
        "-vf", vf_filter,
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-progress", "pipe:1",
        "-nostats",
        "-y",
        output_path,
    ]

    def generate():
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time_pat = re.compile(r"out_time_ms=(\d+)")
        for line in process.stdout:
            m = time_pat.search(line)
            if m and duration > 0:
                t_s      = int(m.group(1)) / 1_000_000
                progress = min(int((t_s / duration) * 95), 95)
                yield f"data: {json.dumps({'progress': progress})}\n\n"
        process.wait()
        if process.returncode == 0:
            yield f"data: {json.dumps({'progress': 100, 'done': True, 'file': output_path})}\n\n"
        else:
            err = process.stderr.read()
            print("FFmpeg error:", err)
            yield f"data: {json.dumps({'error': err})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/download")
def download_file(path: str = Query(...)):
    if not os.path.exists(path):
        return {"error": "file not found"}
    return FileResponse(path, media_type="video/mp4", filename="capify-export.mp4")


@app.get("/health")
def health():
    return {"status": "ok"}