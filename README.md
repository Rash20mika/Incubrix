# IncuBrix Script-to-B-roll Video Engine

A local Windows application that turns a script or uploaded narration into sourced b-roll MP4s using semantically selected still photographs with subtle motion. FastAPI runs the job pipeline and Streamlit provides the authoring UI. Jobs, analysis cache, outputs, and temporary media are local. Set `IMAGE_MODE=false` to use the legacy stock-video/local-video path.

## Requirements

- Windows 10/11, Python 3.10 or newer, Streamlit 1.65.0, and FFmpeg/ffprobe on `PATH`.
- A Groq API key for live script analysis and audio transcription.
- A Pixabay API key for primary stock-image and stock-video search.
- Openverse API credentials (`OPENVERSE_CLIENT_ID`, `OPENVERSE_CLIENT_SECRET`) for its broad, item-licensed image index.
- Optional Unsplash (`UNSPLASH_ACCESS_KEY`) and Pexels (`PEXELS_API_KEY`) keys. Wikimedia Commons and NASA are also searched where configured or subject-relevant; NASA assets without clear item-specific reuse rights are rejected.
- Pillow (installed from `requirements.txt`) and FFmpeg/ffprobe for still-image processing and MP4 rendering.
- An installed local Windows text-to-speech voice for script narration. The chosen voice and its license/usage terms must be checked separately.

No Docker is used. Script analysis has a deterministic fallback when Groq is absent or returns invalid output; that fallback is not equivalent to LLM analysis. Pixabay and Groq credentials are never returned in API responses or manifests.

## Install and configure (PowerShell)

```powershell
cd E:\Incubrix
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set `GROQ_API_KEY` and `PIXABAY_API_KEY` in `.env`; add Openverse and any optional Unsplash/Pexels credentials there as needed. The checked-in `.env.example` contains blank secret placeholders; never place live keys in it. `GROQ_MODEL` defaults to `openai/gpt-oss-20b`; choose a model enabled for your Groq account that supports JSON-object output. Images are discovered across configured official APIs through `IMAGE_PROVIDER_ORDER` (default `pixabay,openverse,wikimedia,unsplash,nasa,pexels`) and video clips through `VIDEO_PROVIDER_ORDER` (default `pixabay,pexels,wikimedia`). Pixabay image search uses the existing Pixabay key; Wikimedia Commons needs no credentials. Openverse is enabled only when both client credentials are configured, and Unsplash/Pexels require their own keys. Provider errors are recorded and do not prevent searches at other sources. The app uses official provider APIs and does not scrape or crawl sites where permission and robots rules cannot be established.

Image pipeline settings include `IMAGE_MODE`, `IMAGE_MIN_RELEVANCE`, `IMAGE_MAX_CANDIDATES_PER_SCENE`, `IMAGE_PROVIDER_TIMEOUT_SECONDS`, `IMAGE_PROVIDER_RETRIES`, `IMAGE_SCENE_MIN_SECONDS`, `IMAGE_SCENE_MAX_SECONDS`, `VIDEO_WIDTH`, `VIDEO_HEIGHT`, `VIDEO_FPS`, `IMAGE_MOTION`, `IMAGE_TRANSITION_SECONDS`, and `IMAGE_ALLOW_REUSE` (off by default). Clean cuts are used by default; nonzero requested transitions are reported but not applied to avoid changing speech timing.

FFmpeg should be installed separately and available in a new terminal:

```powershell
ffmpeg -version
ffprobe -version
```

For script narration, `pyttsx3` uses installed Windows voices. Review available voices and configure `TTS_VOICE_ID` in `.env` if needed. Voice IDs and licensing vary by Windows installation; the app does not claim commercial clearance. If no suitable voice exists, install a voice you are licensed to use rather than expecting the app to make a silent/fake audio file.

## Run

Start the backend in one PowerShell terminal:

```powershell
cd E:\Incubrix
.\.venv\Scripts\Activate.ps1
python main.py
```

Start the UI in another:

```powershell
cd E:\Incubrix
.\.venv\Scripts\Activate.ps1
python -m streamlit run streamlit_app.py --server.address 127.0.0.1
```

Open `http://localhost:8501`. Backend health is at `http://127.0.0.1:8000/health`, readiness at `/ready`, and interactive API docs at `/docs`. You may override `API_URL` for the frontend, and `API_HOST`/`API_PORT` for the backend.

Run the automated suite:

```powershell
python -m pytest -q
```

Batch-process UTF-8 `.txt` scripts:

```powershell
python batch_runner.py .\scripts\one.txt .\scripts\two.txt --ratio 16:9 --style documentary
```

## Inputs and output

Choose exactly one primary input: a non-empty script, an audio upload, or live narration recorded in the Streamlit UI. The browser recorder uses `MediaRecorder` and uploads its native audio container directly, avoiding client-side audio decoding. Supported uploads are MP3, WAV, M4A, WEBM, OGG, and MP4. Allow microphone permission when prompted; browser microphone access requires a secure context (localhost is supported). Audio is duration-limited by `MAX_AUDIO_MINUTES`, transcribed with Groq `whisper-large-v3`, and normalized via FFmpeg. Script text-to-speech is local and loudness-normalized without changing its timing. Optional scene hints, avoid terms, visual style, ratios, captions, and local-footage rights confirmation are available in the UI.

After a request is submitted, IncuBrix analyzes the script, finds and ranks images from the configured provider websites, and renders the video directly. Scene lengths are assigned automatically to cover the narration; the app does not ask the user to choose timings or review a timeline. The generated video, source details, and provider attribution are shown when rendering finishes.

Each job writes its requested `video_16_9.mp4`, `video_9_16.mp4`, and/or `video_1_1.mp4`, `captions.srt`, `timeline.json`, and `manifest.json` beneath `OUTPUT_DIR\<job-id>`. Image and video candidates are ranked against the full narration, scene meaning, subjects, actions, context, and queries. Only candidates meeting the relevance threshold and an explicit rights-verification gate are downloaded; duplicate images and clips are prevented. The timeline records source details, selected query, retrieval time, license, attribution, score, provider failures, and rights rejections. If no relevant rights-cleared media is found, the scene remains in sequence with a nonverbal fallback card. Burned-in captions are off by default; an SRT remains available. The video duration is checked against the actual narration duration with ffprobe.

Local video fallback files belong in `local_footage`. The UI requires the user to confirm reuse rights before those files are considered. That attestation is not independently verified.

## HTTP API

- `GET /health`, `GET /ready`
- `POST /api/v1/broll/jobs` — JSON for script mode or multipart form for script/audio; returns 202 and job ID. Set `prepare_only=true` to pause at the timeline-review stage.
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/result`
- `GET /api/v1/jobs/{job_id}/timeline`
- `GET /api/v1/jobs/{job_id}/manifest`
- `POST /api/v1/jobs/{job_id}/timeline/render` — validate and render the reviewed/customized timeline; accepts `mode` and ordered `scenes` with `scene_id`, `sequence_index`, `start`, `end`, and `selected_visual`
- `GET /api/v1/jobs/{job_id}/files/{filename}` — generated output/preview download

Use the `Idempotency-Key` request header to make a submission idempotent. Errors use an `error` object with uppercase `code`, `message`, `retryable`, and `details`.

## Rights and limitations

Openverse is a multi-source index, not a blanket licence; the adapter accepts only recognized commercial Creative Commons/public-domain licences with item-level licence/source metadata. Unsplash uses the official API and required download tracking. Wikimedia Commons uses its official API and rejects missing/incompatible licence metadata. NASA assets are rejected unless item-specific reuse rights are verified. Pixabay images and video and Pexels images/video rely on their published provider licences; review their current terms and any restrictions on depicted people, property, or marks. Provider metadata cannot establish all third-party rights. See [SOURCES.md](./SOURCES.md).

Relevance is a transparent lexical/metadata heuristic guided by Groq scene descriptions, not a vision model or human review. Scene boundaries for script-only input are estimated by word count; Whisper segment alignment is used where available, then normalized to the narration duration. No word-level timing is claimed. Background music is not mixed. Photos are placed over a blurred, fit-to-canvas background to avoid distorting or aggressively cropping the foreground. Clean cuts preserve narration timing; crossfade overlaps are not currently applied. The render pipeline needs FFmpeg/libass for burned subtitles. Jobs run in-process and persist state to SQLite, so a process shutdown interrupts active work; this is not a multi-worker queue service.
