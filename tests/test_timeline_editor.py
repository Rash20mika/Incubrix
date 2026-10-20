from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from incubrix.audio import probe_duration
from incubrix.analysis import deterministic_analysis
from incubrix.captions import build_srt
from incubrix.config import Settings
from incubrix.jobs import JobProcessor
from incubrix.models import JobRequest
from incubrix.storage import JobStore
from incubrix.timeline_editor import customize_timeline


def scenes() -> list[dict[str, Any]]:
    return [
        {
            "scene_id": "s1", "sequence_index": 0, "text": "First narration.",
            "narration_text": "First narration.", "start": 0.0, "end": 3.0,
            "duration_seconds": 3.0, "selected_image": "assets/first.jpg",
            "source_url": "https://example.org/first", "license": {"name": "CC BY"},
        },
        {
            "scene_id": "s2", "sequence_index": 1, "text": "Second narration.",
            "narration_text": "Second narration.", "start": 3.0, "end": 10.0,
            "duration_seconds": 7.0, "selected_image": "assets/second.jpg",
            "source_url": "https://example.org/second", "license": {"name": "CC BY"},
        },
    ]


def test_automatic_timing_preserves_scene_weights_and_extends_to_audio() -> None:
    result = customize_timeline(scenes(), [
        {"scene_id": "s1", "sequence_index": 0},
        {"scene_id": "s2", "sequence_index": 1},
    ], 20, "automatic")
    assert result[0]["start"] == 0
    assert result[-1]["end"] == 20
    assert result[0]["duration_seconds"] == pytest.approx(6)
    assert result[1]["duration_seconds"] == pytest.approx(14)
    assert result[0]["timing_method"] == "automatic"


def test_equal_timing_divides_full_narration() -> None:
    result = customize_timeline(scenes(), [
        {"scene_id": "s1", "sequence_index": 0},
        {"scene_id": "s2", "sequence_index": 1},
    ], 12, "equal")
    assert [scene["duration_seconds"] for scene in result] == pytest.approx([6, 6])
    assert result[-1]["end"] == 12


def test_frame_allocation_covers_narration_without_losing_scene_boundaries() -> None:
    from incubrix.image_render import _allocate_scene_frames

    rows = [
        {"start": index / 10, "end": (index + 1) / 10}
        for index in range(10)
    ]
    counts = _allocate_scene_frames(rows, 1.0, 10)
    assert counts == [1] * 10
    assert sum(counts) == 10


def test_manual_timing_accepts_contiguous_user_boundaries() -> None:
    result = customize_timeline(scenes(), [
        {"scene_id": "s1", "sequence_index": 0, "start": 0, "end": 4},
        {"scene_id": "s2", "sequence_index": 1, "start": 4, "end": 10},
    ], 10, "manual")
    assert [(scene["start"], scene["end"]) for scene in result] == [(0, 4), (4, 10)]
    assert all(scene["timing_method"] == "manual" for scene in result)


def test_narration_synced_preserves_spoken_order_and_relative_boundaries() -> None:
    result = customize_timeline(scenes(), [
        {"scene_id": "s1", "sequence_index": 0},
        {"scene_id": "s2", "sequence_index": 1},
    ], 20, "narration_synced")
    assert [scene["scene_id"] for scene in result] == ["s1", "s2"]
    assert [scene["duration_seconds"] for scene in result] == pytest.approx([6, 14])
    with pytest.raises(ValueError, match="original narration scene order"):
        customize_timeline(scenes(), [
            {"scene_id": "s2", "sequence_index": 0},
            {"scene_id": "s1", "sequence_index": 1},
        ], 10, "narration_synced")


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ([{"scene_id": "s1", "sequence_index": 0, "start": -1, "end": 5},
          {"scene_id": "s2", "sequence_index": 1, "start": 5, "end": 10}], "positive"),
        ([{"scene_id": "s1", "sequence_index": 0, "start": 0, "end": 6},
          {"scene_id": "s2", "sequence_index": 1, "start": 5, "end": 10}], "contiguous"),
        ([{"scene_id": "s1", "sequence_index": 0, "start": 0, "end": 4},
          {"scene_id": "s2", "sequence_index": 1, "start": 5, "end": 10}], "contiguous"),
        ([{"scene_id": "s1", "sequence_index": 0, "start": 0, "end": 4},
          {"scene_id": "s2", "sequence_index": 1, "start": 4, "end": 12}], "within"),
    ],
)
def test_manual_mode_rejects_invalid_times(edits: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        customize_timeline(scenes(), edits, 10, "manual")


def test_reorder_remove_and_add_visual_only_scene() -> None:
    result = customize_timeline(scenes(), [
        {"scene_id": "s2", "sequence_index": 0},
        {"scene_id": "manual-new", "sequence_index": 1, "selected_visual": ""},
    ], 10, "equal")
    assert [scene["scene_id"] for scene in result] == ["s2", "manual-new"]
    assert result[1]["text"] == ""
    assert result[1]["end"] == 10


def test_caption_timing_is_not_changed_by_visual_timing_edits() -> None:
    original = scenes()
    original_captions = build_srt(original)
    changed = customize_timeline(original, [
        {"scene_id": "s1", "sequence_index": 0, "start": 0, "end": 5},
        {"scene_id": "s2", "sequence_index": 1, "start": 5, "end": 10},
    ], 10, "manual")
    assert build_srt(original) == original_captions
    assert changed[0]["end"] == 5
    assert "00:00:03,000" in original_captions


def test_rerender_preserves_voice_and_replaces_visuals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    settings = Settings(
        output_dir=tmp_path / "outputs", temp_dir=tmp_path / "temp",
        database_path=tmp_path / "jobs.db", image_mode=True, video_fps=10,
    )
    store = JobStore(settings.database_path)
    job_id = "timeline-rerender-test"
    job_dir = settings.output_dir / job_id
    assets_dir = job_dir / "assets"
    assets_dir.mkdir(parents=True)
    for name, color in (("first.jpg", "red"), ("second.jpg", "blue")):
        Image.new("RGB", (64, 64), color).save(assets_dir / name)
    audio = job_dir / "render_source" / "narration.wav"
    audio.parent.mkdir()
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    original_audio_duration = probe_duration(audio)
    timeline_data = {"job_id": job_id, "scenes": scenes(), "warnings": []}
    (job_dir / "timeline.json").write_text(json.dumps(timeline_data), encoding="utf-8")
    captions = build_srt(scenes())
    (job_dir / "captions.srt").write_text(captions, encoding="utf-8")
    payload = {"request": {
        "script": "A script.", "aspect_ratios": ["16:9"],
        "include_srt": True, "burn_captions": False,
    }}
    store.create_job(job_id, payload, None)
    store.update_job(job_id, status="awaiting_timeline", outputs={
        "timeline.json": str(job_dir / "timeline.json"),
        "captions.srt": str(job_dir / "captions.srt"),
    })
    calls: list[tuple[list[Path | None], Path, list[Path | None]]] = []

    def fake_render(
        images: list[Path | None], scene_rows: list[dict[str, Any]], narration: Path,
        output: Path, ratio: str, **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append((images, narration, kwargs["video_paths"]))
        output.write_bytes(b"rendered mp4")
        return {"duration": 1.0, "audio_duration": 1.0, "has_audio": True}

    monkeypatch.setattr("incubrix.jobs.render_image_video", fake_render)
    processor = JobProcessor(settings, store)
    result = processor.rerender_timeline(job_id, {
        "mode": "manual",
        "scenes": [
            {"scene_id": "s2", "sequence_index": 0, "start": 0, "end": 0.5,
             "selected_visual": "assets\\first.jpg"},
            {"scene_id": "s1", "sequence_index": 1, "start": 0.5, "end": 1,
             "selected_visual": "assets/second.jpg"},
        ],
    })
    assert result["timeline"]["scenes"][0]["scene_id"] == "s2"
    assert calls[0][0] == [assets_dir / "first.jpg", assets_dir / "second.jpg"]
    assert calls[0][1] == audio
    assert calls[0][2] == [None, None]
    assert probe_duration(audio) == original_audio_duration
    assert (job_dir / "captions.srt").read_text(encoding="utf-8") == captions
    assert store.get_job(job_id)["status"] == "succeeded"


def test_prepare_job_stops_for_timeline_review_before_video_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    settings = Settings(
        output_dir=tmp_path / "outputs", temp_dir=tmp_path / "temp",
        database_path=tmp_path / "jobs.db", image_mode=True,
        pixabay_enabled=False, video_fps=10,
    )
    store = JobStore(settings.database_path)
    job_id = "prepare-for-review"
    payload = {"request": {
        "script": "Farmers use technology to improve crops.",
        "timeline_mode": "equal", "prepare_only": True,
        "aspect_ratios": ["16:9"], "include_srt": True,
    }}
    store.create_job(job_id, payload, None)
    work = settings.temp_dir / job_id
    out_dir = settings.output_dir / job_id
    out_dir.mkdir(parents=True)
    audio = work / "voice.wav"
    audio.parent.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    image = tmp_path / "discovered.jpg"
    Image.new("RGB", (128, 96), (70, 100, 130)).save(image)
    monkeypatch.setattr(
        "incubrix.jobs.retrieve_scene_images",
        lambda *args, **kwargs: ([image], [], []),
    )
    monkeypatch.setattr(
        "incubrix.jobs.retrieve_scene_video_clips",
        lambda *args, **kwargs: ([None], [], []),
    )
    scenes_result = deterministic_analysis(
        "Farmers use technology to improve crops.", "documentary", [],
    ).scenes
    processor = JobProcessor(settings, store, image_providers={})
    processor._process_image_output(
        job_id, JobRequest.model_validate(payload["request"]),
        work, out_dir, audio, scenes_result,
        [{"scene_id": scenes_result[0].scene_id, "text": scenes_result[0].narration,
          "narration_text": scenes_result[0].narration, "start": 0.0, "end": 1.0,
          "duration_seconds": 1.0}],
        "deterministic_fallback", {}, [], [], "script",
    )
    record = store.get_job(job_id)
    timeline = json.loads(Path(record["outputs"]["timeline.json"]).read_text(encoding="utf-8"))
    assert record["status"] == "awaiting_timeline"
    assert "video_16_9.mp4" not in record["outputs"]
    assert timeline["timing_mode"] == "equal"
    assert (out_dir / "assets" / "scene_0000.jpg").is_file()


def test_timeline_rerender_api_accepts_completed_and_prepared_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import incubrix.api as api

    store = JobStore(tmp_path / "api.db")
    payload = {"request": {"script": "Script.", "aspect_ratios": ["16:9"]}}
    prepared_id = "prepared-job"
    store.create_job(prepared_id, payload, None)
    store.update_job(prepared_id, status="awaiting_timeline")
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "settings", Settings(
        output_dir=tmp_path / "outputs", temp_dir=tmp_path / "temp",
        database_path=tmp_path / "api.db",
    ))
    monkeypatch.setattr(api, "processor", SimpleNamespace(
        rerender_timeline=lambda job_id, body: {
            "timeline": {"job_id": job_id, "scenes": body["scenes"]},
            "manifest": {"timeline": {"timing_mode": body["mode"]}},
        },
    ))
    response = TestClient(api.app).post(
        f"/api/v1/jobs/{prepared_id}/timeline/render",
        json={"mode": "equal", "scenes": [{"scene_id": "s1"}]},
    )
    assert response.status_code == 200
    assert response.json()["manifest"]["timeline"]["timing_mode"] == "equal"


def test_mixed_image_and_clip_render_keeps_audio_and_duration(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    from incubrix.image_render import render_image_video

    image = tmp_path / "scene.jpg"
    Image.new("RGB", (120, 80), (180, 50, 40)).save(image)
    clip = tmp_path / "scene.mp4"
    audio = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=green:s=120x80:r=10:d=0.5", "-an", "-c:v", "libx264", str(clip),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    result = render_image_video(
        [image, None],
        [
            {"scene_id": "image", "sequence_index": 0, "text": "An image.", "start": 0, "end": 0.5},
            {"scene_id": "clip", "sequence_index": 1, "text": "A clip.", "start": 0.5, "end": 1},
        ],
        audio, tmp_path / "mixed.mp4", "16:9", fps=10, motion=False,
        work_dir=tmp_path / "render", landscape_size=(320, 180),
        video_paths=[None, clip],
    )
    assert result["has_audio"] is True
    assert result["duration"] == pytest.approx(1, abs=0.15)
