from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from incubrix.analysis import SceneAnalyzer
from incubrix.audio import FFmpegError, validate_audio
from incubrix.config import Settings
from incubrix.jobs import JobProcessor
from incubrix.manifests import make_manifest, make_timeline
from incubrix.models import SceneAnalysis
from incubrix.providers import Candidate, PixabayProvider
from incubrix.relevance import score_relevance
from incubrix.render import render_video
from incubrix.storage import JobStore


def settings(tmp_path: Path, **kwargs: object) -> Settings:
    options: dict[str, object] = {"pixabay_enabled": False, "image_mode": False}
    options.update(kwargs)
    return Settings(output_dir=tmp_path / "outputs", temp_dir=tmp_path / "temp",
                    database_path=tmp_path / "jobs.db", local_footage_dir=tmp_path / "local",
                    **options)


class MalformedClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs: object) -> object:
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{broken"))])


def test_scene_analysis_malformed_fallback_and_cache(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    analyzer = SceneAnalyzer(settings(tmp_path), store, client=MalformedClient(), retries=0)
    result, method = analyzer.analyze("Cloud computing helps businesses store data remotely.", "documentary", [])
    assert method == "deterministic_fallback_after_groq_error"
    assert result.scenes[0].search_queries[0] == "data center servers digital infrastructure"
    cached, cached_method = analyzer.analyze("Cloud computing helps businesses store data remotely.", "documentary", [])
    assert cached_method == "cache"
    assert cached.model_dump() == result.model_dump()


def test_analysis_rejects_empty_script(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        SceneAnalyzer(settings(tmp_path), JobStore(tmp_path / "jobs.db")).analyze("  ", "documentary", [])


def test_valid_groq_output_preserves_exact_narration_spacing(tmp_path: Path) -> None:
    script = "  First sentence.  Second sentence.\n"
    response = json.dumps({
        "topic": "test", "style": "documentary",
        "scenes": [
            {"scene_id": "one", "narration": "First sentence.", "visual_description": "First visual",
             "search_queries": ["first visual"], "entities": [], "actions": [], "locations": [],
             "mood": "calm", "avoid_constraints": [], "importance": 0.5, "rationale": "Illustrates one."},
            {"scene_id": "two", "narration": "Second sentence.", "visual_description": "Second visual",
             "search_queries": ["second visual"], "entities": [], "actions": [], "locations": [],
             "mood": "calm", "avoid_constraints": [], "importance": 0.5, "rationale": "Illustrates two."},
        ],
    })
    class ValidClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs: object) -> object:
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))])
    result, method = SceneAnalyzer(settings(tmp_path), JobStore(tmp_path / "jobs.db"),
                                   client=ValidClient()).analyze(script, "documentary", [])
    assert method == "groq_json"
    assert "".join(scene.narration for scene in result.scenes) == script


def test_pixabay_parsing_and_missing_metadata() -> None:
    class Response:
        status_code = 200
        def raise_for_status(self) -> None: pass
        def json(self) -> dict[str, object]:
            return {"hits": [
                {"id": 11, "user": "maker", "duration": 12,
                 "videos": {"small": {"url": "https://cdn.example/a.mp4", "width": 640, "height": 360}},
                 "tags": "server, technology"},
                {"id": 12, "videos": {}, "tags": "ignored"},
            ]}
    class Session:
        def get(self, *args: object, **kwargs: object) -> Response:
            return Response()
    provider = PixabayProvider("not-a-real-key", session=Session())  # key never sent to a real service
    matches = provider.search("data center")
    assert len(matches) == 1
    assert matches[0].source_id == "11"
    assert matches[0].contributor == "maker"
    assert matches[0].license_url.startswith("https://pixabay.com/")
    assert PixabayProvider.select_rendition({"small": {"url": "https://cdn", "width": 640, "height": 360}})


def test_pixabay_retries_rate_limit_and_missing_key() -> None:
    class Response:
        def __init__(self, code: int): self.status_code = code
        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(str(self.status_code))
        def json(self) -> dict[str, object]: return {"hits": []}
    class Session:
        calls = 0
        def get(self, *args: object, **kwargs: object) -> Response:
            self.calls += 1
            return Response(429 if self.calls == 1 else 200)
    session = Session()
    provider = PixabayProvider("test", retries=1, session=session)
    provider.search("query")
    assert session.calls == 2
    with pytest.raises(RuntimeError, match="PIXABAY_API_KEY"):
        PixabayProvider("").search("query")


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_MODEL", "configured-model")
    monkeypatch.setenv("PIXABAY_API_KEY", "secret")
    monkeypatch.setenv("OPENVERSE_CLIENT_ID", "openverse-client")
    monkeypatch.setenv("OPENVERSE_CLIENT_SECRET", "private-openverse-secret")
    loaded = Settings.from_env()
    assert loaded.groq_model == "configured-model"
    assert loaded.pixabay_api_key == "secret"
    assert loaded.openverse_client_id == "openverse-client"
    assert loaded.openverse_client_secret == "private-openverse-secret"
    assert "secret" not in json.dumps(loaded.safe_dict())
    assert "private-openverse-secret" not in json.dumps(loaded.safe_dict())


def test_relevance_and_license_provenance() -> None:
    scene = {"narration": "Technicians maintain data center servers", "visual_description": "server racks",
             "search_queries": ["data center"], "entities": ["servers"], "actions": ["maintain"]}
    candidate = {"title": "Data center server maintenance", "tags": "server racks", "width": 1920,
                 "height": 1080, "duration": 10}
    score = score_relevance(scene, candidate)
    assert score.value > 0.6
    assert score.estimated is True
    timeline = make_timeline("job", [{
        "scene_id": "scene-1", "text": "text", "selected_clip": "clip.mp4", "source_url": "https://example.org",
        "provider_id": "id-1", "license": {"name": "CC-BY"}, "coverage_status": "covered",
        "relevance_score": score.value,
    }], [])
    assert timeline["scenes"][0]["license"]["name"] == "CC-BY"


def test_timestamps_manifest_and_coverage() -> None:
    from incubrix.captions import build_srt, srt_timestamp
    assert srt_timestamp(1.2345) == "00:00:01,235"
    assert "00:00:00,000 --> 00:00:02,000" in build_srt([{"text": "Hello", "start": 0, "end": 2}])
    timeline = make_timeline("j", [{"scene_id": "1", "text": "x", "coverage_status": "uncovered"}], ["missing"])
    manifest = make_manifest("j", "model", ["Pixabay"], {}, ["16:9"], [], {"render": 1.2}, ["missing"], [], timeline)
    assert timeline["scenes"][0]["coverage_status"] == "uncovered"
    assert manifest["stage_timings_seconds"]["render"] == 1.2
    assert manifest["provenance"] == []


def test_audio_invalid_type_and_empty_script(tmp_path: Path) -> None:
    settings_obj = settings(tmp_path)
    bad = tmp_path / "bad.aac"
    bad.write_bytes(b"x" * 256)
    with pytest.raises(ValueError, match="Unsupported"):
        validate_audio(bad, settings_obj)
    with pytest.raises(Exception):
        SceneAnalysis.model_validate({"topic": "", "style": "", "scenes": []})
    invalid_audio = tmp_path / "corrupt.mp3"
    invalid_audio.write_bytes(b"x" * 512)
    with pytest.raises(Exception):
        validate_audio(invalid_audio, settings_obj)


def test_transcription_requires_groq_key(tmp_path: Path) -> None:
    from incubrix.audio import transcribe_audio
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        transcribe_audio(tmp_path / "does-not-need-to-exist.wav", settings(tmp_path))


def test_render_missing_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import incubrix.render as renderer
    monkeypatch.setattr(renderer.shutil, "which", lambda _: None)
    with pytest.raises(FFmpegError, match="FFmpeg"):
        render_video([None], [1], tmp_path / "narration.wav", tmp_path / "out.mp4", "16:9")


def test_api_health_validation_and_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import incubrix.api as api
    api_store = JobStore(tmp_path / "api.db")
    monkeypatch.setattr(api, "store", api_store)
    monkeypatch.setattr(api, "processor", SimpleNamespace(run=lambda *args: None))
    monkeypatch.setattr(api, "settings", settings(tmp_path, image_mode=True, pixabay_enabled=True))
    assert api.ready().status_code == 200
    assert json.loads(api.ready().body)["capabilities"]["audio_transcription"] is False
    client = TestClient(api.app)
    assert client.get("/health").status_code == 200
    assert client.post("/api/v1/broll/jobs", json={"script": "  "}).status_code == 422
    first = client.post("/api/v1/broll/jobs", json={"script": "A valid script.", "aspect_ratios": ["1:1"]},
                        headers={"Idempotency-Key": "repeat"})
    second = client.post("/api/v1/broll/jobs", json={"script": "A valid script.", "aspect_ratios": ["1:1"]},
                         headers={"Idempotency-Key": "repeat"})
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    form_response = client.post("/api/v1/broll/jobs", data={
        "script": "Script submitted from the Streamlit form.",
        "style": "documentary",
        "aspect_ratios": '["16:9"]',
        "avoid_content": "[]",
        "voice_settings": "{}",
        "burn_captions": "true",
        "include_srt": "true",
        "confirm_local_rights": "false",
        "scene_list": "[]",
        "timeline_mode": "equal",
        "prepare_only": "true",
    })
    assert form_response.status_code == 202
    form_job = api_store.get_job(form_response.json()["job_id"])
    assert form_job["payload"]["request"]["timeline_mode"] == "equal"
    assert form_job["payload"]["request"]["prepare_only"] is True
    recorded_audio_response = client.post(
        "/api/v1/broll/jobs",
        data={
            "aspect_ratios": '["16:9"]',
            "avoid_content": "[]",
            "voice_settings": "{}",
            "scene_list": "[]",
        },
        files={"audio": ("voice_recording.wav", b"x" * 128, "audio/wav")},
    )
    assert recorded_audio_response.status_code == 503
    assert recorded_audio_response.json()["error"]["code"] == "TRANSCRIPTION_NOT_CONFIGURED"
    monkeypatch.setattr(api, "settings", settings(tmp_path, image_mode=True, pixabay_enabled=True,
                                                  groq_api_key="test-key"))
    assert json.loads(api.ready().body)["capabilities"]["audio_transcription"] is True
    configured_audio_response = client.post(
        "/api/v1/broll/jobs",
        data={"aspect_ratios": '["16:9"]', "avoid_content": "[]", "voice_settings": "{}", "scene_list": "[]"},
        files={"audio": ("voice_recording.wav", b"x" * 128, "audio/wav")},
    )
    assert configured_audio_response.status_code == 202


def test_browser_recorder_upload_is_staged_and_retrievable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import incubrix.api as api

    monkeypatch.setattr(api, "settings", settings(tmp_path, image_mode=True, groq_api_key="test-key"))
    monkeypatch.setattr(api, "validate_audio", lambda *_: 2.5)
    token = uuid.uuid4().hex
    client = TestClient(api.app)
    uploaded = client.post(
        f"/api/v1/recordings/{token}",
        files={"audio": ("voice_recording.webm", b"recorded audio bytes" * 16, "audio/webm")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["duration_seconds"] == 2.5

    retrieved = client.get(f"/api/v1/recordings/{token}")
    assert retrieved.status_code == 200
    assert retrieved.content == b"recorded audio bytes" * 16
    assert retrieved.headers["x-audio-extension"] == ".webm"
    assert client.delete(f"/api/v1/recordings/{token}").json()["deleted"] is True


def test_recorder_html_records_encoded_media_without_audio_context() -> None:
    from incubrix.recorder import recorder_html

    html = recorder_html("http://127.0.0.1:8000/api/v1/recordings/token", 600)
    assert "new MediaRecorder" in html
    assert "decodeAudioData" not in html
    assert 'mode: "no-cors"' in html
    assert "api/v1/recordings/token" in html


def test_webm_duration_falls_back_to_ffmpeg_decoding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import incubrix.audio as audio

    recording = tmp_path / "voice.webm"
    recording.write_bytes(b"recording" * 32)
    probes: list[Path] = []
    ffmpeg_calls: list[list[str]] = []

    def probe(path: Path) -> float:
        probes.append(path)
        if path.suffix == ".webm":
            raise ValueError("Audio duration could not be determined")
        return 2.5

    monkeypatch.setattr(audio, "probe_duration", probe)
    monkeypatch.setattr(audio, "run_ffmpeg", lambda args: ffmpeg_calls.append(args))

    assert audio.validate_audio(recording, settings(tmp_path)) == 2.5
    assert ffmpeg_calls and str(recording) in ffmpeg_calls[0]
    assert len(probes) == 2


def _make_synthetic_media(folder: Path) -> tuple[Path, Path]:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not available")
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "stock.mp4"
    audio = folder / "voice.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x180:r=24:d=3", "-an", "-c:v", "libx264", str(video)],
                   check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3", "-c:a", "pcm_s16le", str(audio)],
                   check=True, capture_output=True)
    return video, audio


def test_mocked_end_to_end_duplicate_coverage_and_aspect_ratio(tmp_path: Path) -> None:
    video, _ = _make_synthetic_media(tmp_path / "media")
    store = JobStore(tmp_path / "jobs.db")
    config = settings(tmp_path)
    class FakeTTS:
        def synthesize(self, text: str, destination: Path, voice_settings: object) -> Path:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=3", "-c:a", "pcm_s16le", str(destination)],
                           check=True, capture_output=True)
            return destination
    candidate = Candidate("Pixabay", "repeat-id", "https://pixabay.com/videos/id-repeat-id/",
                          contributor="tester", license_name="Pixabay Content License",
                          license_url="https://pixabay.com/service/license-summary/",
                          download_url="https://unused.example/file.mp4", tags="technology server",
                          rights_verified=True)
    class FakeProvider:
        def search(self, query: str) -> list[Candidate]: return [candidate]
        def download(self, item: Candidate, destination: Path) -> Path:
            shutil.copy2(video, destination)
            return destination
    processor = JobProcessor(config, store, pixabay=FakeProvider(), tts=FakeTTS())
    payload = {"request": {"script": "Servers support business. Teams manage data.",
                           "aspect_ratios": ["16:9", "9:16", "1:1"], "burn_captions": False, "include_srt": True}}
    store.create_job("e2e", payload, None)
    processor.run("e2e", payload)
    record = store.get_job("e2e")
    assert record["status"] == "succeeded", record["error"]
    assert all(Path(record["outputs"][name]).is_file() for name in
               ("video_16_9.mp4", "video_9_16.mp4", "video_1_1.mp4"))
    timeline = json.loads(Path(record["outputs"]["timeline.json"]).read_text())
    covered = [scene for scene in timeline["scenes"] if scene["coverage_status"] == "covered"]
    assert len(covered) == 1
    assert covered[0]["license"]["rights_verified"] is True
    assert covered[0]["license"]["verification_method"] == "provider_license_metadata"
    expected = {"video_16_9.mp4": (1920, 1080), "video_9_16.mp4": (1080, 1920),
                "video_1_1.mp4": (1080, 1080)}
    for name, size in expected.items():
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                                 "-of", "json", record["outputs"][name]],
                                check=True, capture_output=True, text=True)
        dimensions = json.loads(result.stdout)["streams"][0]
        assert (dimensions["width"], dimensions["height"]) == size
