# IncuBrix Video Studio — Assessment Evidence Report

**Audit scope:** source, tests, API/OpenAPI files, documentation, and generated files under `outputs/` in this checkout.  
**Audit date:** 2026-10-20.  
**Assessment:** generated outputs, documentation, and test sources are present, but the implementation package is missing from the current checkout. Several requirements also need live demonstrations, visual review, or stronger safety checks. See the [assessment checklist](docs/Assessment_Checklist.md) for the evidence to submit per requirement.

## Current checkout validation blocker

At the latest validation, the `incubrix/` Python package imported by `main.py`, `streamlit_app.py`, and the tests was absent from this checkout. The current full test run therefore fails during collection with `ModuleNotFoundError: No module named 'incubrix'`; the Streamlit AppTest also fails for the same missing package. See the captured [pytest output](docs/evidence/pytest-full-run.txt) and [AppTest output](docs/evidence/streamlit-apptest.txt). The implementation details below reflect the earlier code review and should be treated as historical until the missing source package is restored and the checks are rerun. No source files were recreated as part of this evidence task.

## Project overview and architecture

IncuBrix is a local Windows script/audio-to-video application. Streamlit accepts a script or an audio upload and displays job progress and results. FastAPI validates submissions, stores jobs in SQLite, and schedules job processing. The job processor creates narration for scripts with local `pyttsx3`, or validates, transcribes, and normalizes uploaded audio with FFmpeg and Groq Whisper. Scene analysis uses Groq JSON when configured and falls back to deterministic sentence-based analysis. It discovers provider media, ranks candidates, creates per-scene metadata, renders MP4 output with FFmpeg, and writes a timeline, manifest, and optional SRT.

The default image pipeline uses official image APIs and can also use video clips where available. If no qualifying image or clip is found, it renders a nonverbal fallback visual. Jobs and outputs are local; processing is not a durable distributed queue.

## Assessment against the eight requirements

### 1. Input support — **Partial**

- Earlier source review found support for a script and audio upload as alternative inputs, with MP3, WAV, and M4A among the accepted formats and `MAX_AUDIO_MINUTES` defaulting to 10. The source package is now missing from this checkout, so that behavior cannot be revalidated here.
- Script narration uses the installed local TTS voice and normalizes the resulting audio. Uploaded audio is validated, sent to Groq Whisper for transcription, and normalized with FFmpeg. Transcription requires a configured Groq key.
- Earlier code review found API validation for missing/both primary inputs and unsupported audio extensions. The latest AppTest attempt fails because the package imported by the UI is missing; no current successful app run is evidenced.
- The full suite does **not** demonstrate a real TTS voice, a real Groq transcription, successful upload of each of MP3/WAV/M4A, or the 10-minute boundary against a live API. The existing end-to-end script test uses fake TTS. The live API was unavailable during this audit, so the assessment's proposed live submissions were not captured here.

### 2. Scene segmentation and narration timing — **Partial**

- Groq scene analysis is instructed to split at sentence/phrase boundaries, preserve the original order, and cover the exact input narration. The response validator checks exact concatenated script text. The deterministic fallback splits on sentence punctuation and newlines.
- Script timing is estimated from scene word counts, then scaled to the audio duration. Whisper segment timings are grouped to scenes when supplied, but those boundaries are estimates rather than verified forced alignment. The renderer allocates frames through the full narration duration.
- Existing test sources cover exact narration preservation, scene order, contiguous timeline coverage, automatic timing, and frame allocation. They cannot currently be run because their implementation imports are unavailable. Generated output timelines include scene text and start/end values; stored MP4 duration was probed.
- This does not establish that all scene cuts land on spoken phrase boundaries. Several older `timeline.json` files omit `narration_duration_seconds`; their last scene end still closely tracks the probed video duration, but an explicit transcript/audio comparison has not been made for every demo. No live audio transcript comparison or manual video review was performed.

### 3. Media licensing and provenance — **Partial**

- Selected provider assets retain provider/source IDs, source URLs, license name and URL, creator/attribution, and rights metadata in the scene timeline and manifest. The image pipeline rejects assets without its provider metadata rights gate and a license URL. Wikimedia/Openverse apply license filters; Pixabay, Pexels, and Unsplash adapters attach their published license metadata.
- Existing sample job `21995e1f-9cbe-4dc7-aa2a-d5c503657428` has six scenes with source URLs, license names, and attribution text recorded in its timeline. These are provider-reported records, not independent commercial-rights clearance.
- The code does not detect watermarks or independently verify the rights of depicted people/property, brands, or every source file. No one watched and checked every selected clip for watermarks. NASA candidates carry unverified rights in the adapter and are rejected by the current image retrieval rights gate; an older output nevertheless records a NASA item, so it should not be submitted as proof of current eligibility without reviewing the specific file and terms.
- A verified code defect was fixed during this audit: `ai_generated` had reflected AI-assisted scene analysis rather than whether the media itself was generated. New jobs now record `ai_generated: false` for this stock/procedural-visual pipeline and record `scene_analysis_method` separately. Older output timelines with `ai_generated: true` are historical/misleading and are not proof of generated media. The app does not currently generate AI images or video clips.

### 4. Video quality and aspect ratios — **Partial**

- The image renderer targets 1920×1080, 1080×1920, or 1080×1080 according to the requested ratio, emits H.264/yuv420p with the configured frame rate and AAC audio, and checks stream presence, output profile, frame rate, and duration against the narration.
- Test sources describe the three output dimensions, mixed image/clip rendering, audio presence, silent-audio rejection, and duration tolerance, but cannot currently be run because the implementation package is missing. `docs/evidence/generated-video-probe.txt` records ffprobe output for the 12 stored demo MP4s inspected on this audit date. All 12 are 16:9 1920×1080, 30 fps, H.264/yuv420p, AAC. Only four timelines record narration duration for comparison; this metadata comparison is not a comprehensive audio-drift test.
- No stored portrait or square demo output was found. Eight of the 12 stored timelines omit an explicit narration-duration field. Neither the tests nor this audit performed black-frame or frozen-frame detection, perceptual inspection of every frame, or a formal audio-drift analysis across every requested ratio. Thus those quality claims remain unverified.

### 5. Visual relevance and duplicate prevention — **Partial**

- Candidate selection uses a heuristic score over scene meaning, narration, entities, actions, context, search queries, image size, and aspect ratio. It tries alternative queries/providers, enforces a minimum threshold, and can fall back when no candidate qualifies.
- Test sources cover provider fallback, irrelevant candidates, duplicate provider IDs/URLs, perceptually similar images, corrupt candidates, and an end-to-end generated job. These tests cannot currently be run; the algorithm description is from the earlier source review.
- No human scene-by-scene review or 1–5 relevance score was recorded for the demo files. Do not submit or report human relevance scores based on these automated scores.

### 6. Output safety — **No**

- No implementation was found that identifies realistic faces, named people, logos/brands, or sensitive-news topics; no corresponding adversarial safety tests were found. Scene-analysis instructions discourage inventing factual entities, but that is not an output safety control and cannot guarantee safe stock search results.
- The pipeline uses sourced stock media and procedural fallback graphics, not a generative media model. That reduces AI-generated-face/logo risk but does not prevent retrieval of a real person, a visible brand, or insensitive imagery. The corrected `ai_generated` metadata is not a substitute for these safeguards.
- Evidence of safe output would require new controls and tests plus a documented human review of named-person, brand, and sensitive-news hard cases.

### 7. Job resilience and fallback — **Partial**

- FastAPI creates jobs and schedules processing after acceptance; the UI polls job state/progress. Per-stage progress is persisted. Provider search/download failures are recorded and the image pipeline tries other candidates/providers or retains fallback scenes.
- The test source includes provider timeout/secondary-provider coverage and `test_image_job_completes_with_fallback_when_provider_fails`. Its result cannot currently be confirmed because test collection fails on the missing package.
- This verifies the automated failure/fallback path, not a live exercise switching a provider off during a running demo. Job execution is in-process and is interrupted if the API process exits; it is not a durable queue.

### 8. Integration standard and live demo — **Partial**

- Earlier code review found FastAPI routes for health/readiness, submission, job status/result, timeline/manifest, and output download. Request models and API test sources cover validation, idempotency, recording upload, status, and output-related flows. The package is currently unavailable, so these routes and tests could not be exercised in this validation.
- The checked-in `openapi.yaml` is incomplete/inconsistent with the implementation: it omits recording and timeline-render routes and describes a script-only JSON request despite audio uploads. No contract test compares this file with FastAPI's generated schema.
- The latest Streamlit AppTest failed because the imported package was missing. An earlier audit reported that the UI returned HTTP 200 while no API process was listening on port 8000; `/health` and live job submission were not verified. No review-period recording, submission screenshots, or live-demo timestamps were captured.

## Tests and actual results

Executed on 2026-10-20 using `C:\Program Files\Python310\python.exe` from `E:\Incubrix`:

```powershell
& 'C:\Program Files\Python310\python.exe' -m pytest -q
```

**Result:** failed during collection: 4 test modules could not import `incubrix`; 1 Starlette/AnyIO deprecation warning was also reported. No tests ran successfully in this captured invocation. See [pytest output](docs/evidence/pytest-full-run.txt).

An earlier audit run before the current checkout state reported **60 passed, 1 skipped, 1 warning**. That result is not reproducible from the current checkout and its original console log is not available here; do not submit it as the current test result.

Also executed:

```powershell
& 'C:\Program Files\Python310\python.exe' -c "from streamlit.testing.v1 import AppTest; app=AppTest.from_file('streamlit_app.py').run(); print('AppTest exceptions:', [str(x) for x in app.exception]); assert not app.exception"
```

**Result:** failed: `streamlit_app.py` could not import `incubrix.recorder`; AppTest did not verify app startup/widgets. See [AppTest output](docs/evidence/streamlit-apptest.txt).

An earlier AppTest run was reported as passing before the package disappeared from the current checkout. Its original console log is not available here.

The MP4 inspection used FFprobe, not visual review. See [generated-video probe summary](docs/evidence/generated-video-probe.txt). No black/frozen-frame or watermark scan was run.

## Evidence files and demo records

Use these existing repository-relative files as evidence:

- **Script-generated sample:** [timeline](outputs/21995e1f-9cbe-4dc7-aa2a-d5c503657428/timeline.json), [manifest](outputs/21995e1f-9cbe-4dc7-aa2a-d5c503657428/manifest.json), [captions](outputs/21995e1f-9cbe-4dc7-aa2a-d5c503657428/captions.srt), [16:9 video](outputs/21995e1f-9cbe-4dc7-aa2a-d5c503657428/video_16_9.mp4). Timeline `generated_at`: `2026-10-20T15:45:02.815690+00:00`. This timestamp is file metadata, not a live-demo recording timestamp.
- **Additional script sample:** [timeline](outputs/fcc15c24-d513-498c-bc2b-05b321af6220/timeline.json), [manifest](outputs/fcc15c24-d513-498c-bc2b-05b321af6220/manifest.json), [captions](outputs/fcc15c24-d513-498c-bc2b-05b321af6220/captions.srt), [16:9 video](outputs/fcc15c24-d513-498c-bc2b-05b321af6220/video_16_9.mp4). Timeline `generated_at`: `2026-10-20T11:52:35.904799+00:00`.
- **Available test and integration artifacts:** [API test source](tests/test_core.py), [image/provider/render test source](tests/test_images.py), [timing test source](tests/test_timeline_editor.py), [OpenAPI file](openapi.yaml), [application entry point](main.py), and [Streamlit UI source](streamlit_app.py). The implementation modules imported by these files are not present in the current checkout.

These stored jobs are sample outputs, not evidence that the live-demo requirements were executed during this audit. Restore the missing `incubrix/` package before relying on test sources or demonstrating the app. The checklist lists which files to submit for each requirement and what must still be captured.

## Known limitations and unresolved work

1. Run and record live script/TTS and MP3, WAV, and M4A submissions, including the 10-minute validation boundary and transcription output. Do not include credentials or private narration in evidence.
2. Record a transcript/audio/timeline comparison and review scene changes against spoken boundaries.
3. Manually verify each media item’s current license, commercial-use conditions, source link, attribution, and watermark status. Review the NASA asset in the historical sample before using it as evidence.
4. Generate and inspect 9:16 and 1:1 demo files; add/record black-frame, freeze, and per-ratio audio-sync checks.
5. Watch every scene in the selected demo and keep a dated reviewer score sheet if 1–5 relevance scores are required.
6. Implement and test explicit named-person, brand/logo, face, and sensitive-topic safeguards; the current system does not meet this requirement.
7. Demonstrate provider disable/failure live while retaining a test log and completed fallback output.
8. Align `openapi.yaml` with actual routes and multipart validation, add a schema contract test, and record a successful live submission/status/download flow.
