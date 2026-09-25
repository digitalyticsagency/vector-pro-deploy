# Shopify to Video AI Engine

Daily worker that pulls products from the Shopify GraphQL Admin API, asks Gemini for a
structured 3-scene script, and renders a 10-second vertical MP4 (1080x1920, 30fps, H.264) with MoviePy.

| Scene | Length | Purpose |
|---|---|---|
| Hook | 3.0s | Stop the scroll |
| Core value | 4.0s | Main benefit |
| Call to action | 3.0s | CTA and price |

## Files

- `config.py` settings from env vars / `.env`
- `shopify_client.py` GraphQL client, HTML stripping, missing field handling
- `ai_orchestrator.py` Gemini call with a Pydantic `response_schema` (JSON mode) plus retries
- `video_engine.py` image download, cover crop, Ken Burns zoom, centered bold captions, H.264 export
- `main.py` FastAPI app, manual trigger, built-in daily scheduler, status endpoint

## Run

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
uvicorn main:app --host 0.0.0.0 --port 8000
```

- `POST /api/generate-daily-videos` start a run now (returns 202, 409 if one is already running)
- `GET /api/status` running flag, next scheduled run, last run summary
- `GET /health`

Videos land in `OUTPUT_DIR`. Every finished video is also appended to `OUTPUT_DIR/manifest.jsonl`
with `status: ready_for_publishing`, so a social posting job can pick them up.

## Notes

- FFmpeg ships with `imageio-ffmpeg` (pulled in by MoviePy), so no system install is needed.
  ImageMagick is not needed either, MoviePy 2.x draws text with Pillow.
- Captions need a bold `.ttf` font. The default is DejaVu Sans Bold (Debian/Ubuntu `fonts-dejavu-core`).
  Point `FONT_PATH` at your brand font to change the look.
- Rendering is CPU heavy (around 1 to 1.5 minutes per video on 4 cores) and runs in a worker thread
  so the API stays responsive.
- Run a single uvicorn worker, otherwise each worker starts its own scheduler.
