# Capify Backend

FastAPI + FFmpeg rendering service for [Capify](https://github.com/Nabeel-99/capify).

## What it does
- **`POST /export`**: burns animated captions into a video. Builds an ASS subtitle file for the chosen animation (karaoke word highlight, fade or typewriter), font and colours, handles portrait crops, and renders it with FFmpeg
- **`POST /assemble`**: builds a narrated video from images and a voiceover. Each image becomes a scene with a zoom effect, scenes are joined with crossfades, the video is matched to the audio length and optional background music is mixed in
- **`POST /trim`**, **`GET /download`**, **`GET /health`**

Long renders stream their progress back to the client with Server-Sent Events.

## Tech stack
Python, FastAPI, FFmpeg, Docker

## Running locally

Requires FFmpeg installed.

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Or with Docker:

```bash
docker build -t capify-be .
docker run -p 8000:8000 -e PORT=8000 capify-be
```

Optional environment variables: `FFMPEG_PATH`, `FFPROBE_PATH` (default to `ffmpeg` / `ffprobe` on the PATH).

Video rendering is CPU- and memory-heavy, so small free-tier hosts (around 512 MB) may run out of memory.
