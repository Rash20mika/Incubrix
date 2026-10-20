from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image

from incubrix.analysis import deterministic_analysis
from incubrix.audio import FFmpegError
from incubrix.config import Settings
from incubrix.image_pipeline import plan_scene_timeline, retrieve_scene_images, score_image
from incubrix.image_render import PROFILES, render_image_video
from incubrix.images import (
    ImageCandidate, ImageProviderError, OpenverseImageProvider, PexelsImageProvider, UnsplashProvider,
    PixabayImageProvider, WikimediaImageProvider, make_image_providers,
)
from incubrix.manifests import make_manifest, make_timeline
from incubrix.jobs import JobProcessor
from incubrix.image_pipeline import retrieve_scene_video_clips
from incubrix.providers import Candidate, PexelsVideoProvider
from incubrix.storage import JobStore


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    options: dict[str, object] = {
        "pixabay_enabled": False,
        "image_mode": True,
        "image_provider_order": ("pixabay", "openverse", "wikimedia", "unsplash", "nasa", "pexels"),
        "image_min_relevance": 0.05,
        "image_provider_retries": 0,
    }
    options.update(kwargs)
    return Settings(
        output_dir=tmp_path / "outputs", temp_dir=tmp_path / "temp",
        database_path=tmp_path / "jobs.db", local_footage_dir=tmp_path / "local",
        **options,
    )


def _candidate(asset_id: str, title: str = "farmers using agricultural crop technology") -> ImageCandidate:
    return ImageCandidate(
        provider="Mock images", asset_id=asset_id, source_url=f"https://source.example/{asset_id}",
        image_url=f"https://cdn.example/{asset_id}.jpg", width=1600, height=1000,
        title=title, description=title, creator="Photographer", license_name="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution_text=f"Photo by Photographer ({asset_id})", rights_verified=True,
    )


class MockImageProvider:
    name = "Mock images"

    def __init__(self, candidates: list[ImageCandidate], fail_search: bool = False,
                 corrupt_ids: set[str] | None = None):
        self.candidates = candidates
        self.fail_search = fail_search
        self.corrupt_ids = corrupt_ids or set()
        self.calls = 0
        self.queries: list[str] = []
        self.downloaded: list[str] = []

    def search(self, query: str, limit: int = 10) -> list[ImageCandidate]:
        self.calls += 1
        self.queries.append(query)
        if self.fail_search:
            raise ImageProviderError(self.name, "request timed out")
        return self.candidates[:limit]

    def mark_download(self, candidate: ImageCandidate) -> None:
        pass

    def download(self, candidate: ImageCandidate, destination: Path) -> Path:
        self.downloaded.append(candidate.asset_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if candidate.asset_id in self.corrupt_ids:
            destination.write_bytes(b"not an image")
        else:
            Image.new("RGB", (1600, 1000), (30, 120, 70)).save(destination, format="JPEG")
        return destination


def test_three_semantic_scenes_keep_narrative_order() -> None:
    script = (
        "Farmers adopt technology to improve crop production. "
        "Satellite imagery helps monitor agricultural land. "
        "Trust takes time to build."
    )
    analysis = deterministic_analysis(script, "documentary", [])
    assert [scene.sequence_index for scene in analysis.scenes] == [0, 1, 2]
    assert [scene.narration for scene in analysis.scenes] == [
        "Farmers adopt technology to improve crop production.",
        "Satellite imagery helps monitor agricultural land.",
        "Trust takes time to build.",
    ]
    assert "satellite" in analysis.scenes[1].primary_search_query.lower()
    assert "bridge" in analysis.scenes[2].primary_search_query.lower()


def test_complex_scene_has_query_alternatives_and_semantic_score() -> None:
    analysis = deterministic_analysis(
        "Farmers are increasingly adopting technology to improve crop production.", "documentary", [],
    )
    scene = analysis.scenes[0]
    assert scene.core_meaning
    assert scene.alternative_search_queries
    assert "smart farming" in scene.primary_search_query
    score = score_image(scene, _candidate("good"), "16:9")
    assert score.value > 0
    assert "Core-meaning match" in score.reason


def test_openverse_normalizes_only_commercially_reusable_licensed_results() -> None:
    class Response:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return self.payload

    class Session:
        def post(self, *args: object, **kwargs: object) -> Response:
            return Response({"access_token": "temporary-token", "expires_in": 3600})

        def get(self, url: str, **kwargs: object) -> Response:
            assert kwargs["params"]["license"] == "cc0,by,by-sa,pdm"
            assert kwargs["headers"]["Authorization"] == "Bearer temporary-token"
            return Response({"results": [
                {
                    "id": "cc-by-photo", "title": "Farmers monitor crop fields",
                    "url": "https://cdn.example/farmers.jpg",
                    "foreign_landing_url": "https://source.example/farmers",
                    "creator": "A Photographer", "license": "by-4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "width": 1600, "height": 1000,
                },
                {
                    "id": "nc-photo", "title": "Not for commercial reuse",
                    "url": "https://cdn.example/nc.jpg",
                    "foreign_landing_url": "https://source.example/nc",
                    "license": "by-nc", "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
                },
            ]})

    provider = OpenverseImageProvider("client-id", "client-secret", session=Session())
    candidates = provider.search("farmers crop monitoring")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.asset_id == "cc-by-photo"
    assert candidate.rights_verified
    assert candidate.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert candidate.attribution_text
    assert candidate.metadata["commercial_use_allowed_by_license"] is True


def test_openverse_is_an_optional_configurable_broad_image_provider() -> None:
    providers = make_image_providers(
        _settings(Path("unused"), openverse_client_id="client", openverse_client_secret="secret"),
    )
    assert "openverse" in providers
    assert "openverse" not in make_image_providers(_settings(Path("unused")))


def test_pixabay_image_search_normalizes_candidates_and_uses_configured_key() -> None:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"hits": [{
                "id": 42,
                "pageURL": "https://pixabay.com/photos/farmers-crops-42/",
                "largeImageURL": "https://pixabay.com/get/farmers.jpg",
                "imageWidth": 1920,
                "imageHeight": 1080,
                "tags": "farmers, crops, technology",
                "user": "Ada",
            }]}

    class Session:
        def get(self, url: str, **kwargs: object) -> Response:
            assert url == "https://pixabay.com/api/"
            assert kwargs["params"]["key"] == "pixabay-test-key"
            assert kwargs["params"]["q"] == "farmers crops"
            assert kwargs["params"]["image_type"] == "photo"
            assert kwargs["params"]["safesearch"] == "true"
            return Response()

    provider = PixabayImageProvider("pixabay-test-key", session=Session())
    candidate = provider.search("farmers crops", limit=1)[0]
    assert candidate.provider == "Pixabay"
    assert candidate.asset_id == "42"
    assert candidate.creator == "Ada"
    assert candidate.width == 1920 and candidate.height == 1080
    assert candidate.rights_verified
    assert candidate.license_url == PixabayImageProvider.license_url
    assert candidate.metadata["tags"] == "farmers, crops, technology"

    configured = make_image_providers(
        _settings(Path("unused"), pixabay_api_key="pixabay-test-key"),
    )
    assert isinstance(configured["pixabay"], PixabayImageProvider)


def test_provider_normalization_and_optional_pexels() -> None:
    class Response:
        def __init__(self, payload: dict[str, object]):
            self.payload = payload
            self.headers = {}
            self.status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, object]:
            return self.payload
    class Session:
        def __init__(self):
            self.headers = {}
        def get(self, url: str, **kwargs: object) -> Response:
            if "unsplash" in url:
                return Response({"results": [{
                    "id": "u1", "width": 1200, "height": 800,
                    "urls": {"regular": "https://images.example/u1.jpg"},
                    "links": {"html": "https://unsplash.com/photos/u1", "download_location": "https://api.unsplash.com/photos/u1/download"},
                    "user": {"name": "Ada", "username": "ada", "links": {"html": "https://unsplash.com/@ada"}},
                    "alt_description": "farmers in crop field",
                }]})
            return Response({"photos": [{
                "id": 2, "width": 1000, "height": 800, "url": "https://pexels.com/photo/2",
                "photographer": "Bo", "alt": "field", "src": {"large": "https://images.example/2.jpg"},
            }]})
    session = Session()
    unsplash = UnsplashProvider("test-key", session=session)
    normalized = unsplash.search("farmers")
    assert normalized[0].asset_id == "u1"
    assert normalized[0].creator == "Ada"
    assert normalized[0].download_location.endswith("/download")
    pexels = PexelsImageProvider("pexels-key", session=session)
    assert pexels.search("field")[0].provider == "Pexels"
    config = _settings(Path("unused"))
    providers = make_image_providers(config, session_factory=Session)
    assert "pexels" not in providers


def test_wikimedia_rejects_unverified_or_nc_rights() -> None:
    provider = WikimediaImageProvider(commercial_use=True)
    assert provider._license_permits("CC BY-SA 4.0")
    assert provider._license_permits("CC0 1.0")
    assert not provider._license_permits("CC BY-NC 4.0")
    assert not provider._license_permits("")


def test_http_429_uses_bounded_retry() -> None:
    import requests

    class Response:
        status_code = 429
        def raise_for_status(self) -> None:
            error = requests.HTTPError("rate limited")
            error.response = self
            raise error
    class Session:
        calls = 0
        def get(self, *args: object, **kwargs: object) -> Response:
            self.calls += 1
            return Response()
    session = Session()
    provider = UnsplashProvider("test-key", retries=1, session=session)
    with pytest.raises(ImageProviderError) as failure:
        provider.search("earth imagery")
    assert failure.value.status_code == 429
    assert session.calls == 2


def test_provider_timeout_does_not_block_secondary_provider(tmp_path: Path) -> None:
    analysis = deterministic_analysis("Farmers use technology for crop production.", "documentary", [])
    primary = MockImageProvider([], fail_search=True)
    secondary = MockImageProvider([_candidate("success")])
    images, warnings, errors = retrieve_scene_images(
        analysis.scenes, plan_scene_timeline(analysis.scenes, 3), tmp_path,
        _settings(tmp_path), {"unsplash": primary, "wikimedia": secondary},
    )
    assert images[0] is not None
    assert primary.calls > 0 and secondary.calls > 0
    assert errors
    assert not warnings


def test_broad_discovery_tries_alternative_queries_and_other_providers(tmp_path: Path) -> None:
    scene = deterministic_analysis(
        "Farmers use technology to monitor crops.", "documentary", [],
    ).scenes[0]
    unrelated = MockImageProvider([_candidate("unrelated", "night sky and city skyline")])
    relevant = MockImageProvider([_candidate("relevant", "farmers monitoring crop technology")])
    unrelated.name = "Openverse mock"
    unrelated.candidates[0].provider = unrelated.name
    relevant.name = "Wikimedia mock"
    relevant.candidates[0].provider = relevant.name
    settings = _settings(
        tmp_path,
        image_provider_order=("openverse", "wikimedia"),
        image_max_candidates_per_scene=10,
        image_min_relevance=0.15,
    )
    timeline = plan_scene_timeline([scene], 4)
    paths, _, _ = retrieve_scene_images(
        [scene], timeline, tmp_path, settings,
        {"openverse": unrelated, "wikimedia": relevant},
    )
    assert paths[0] is not None
    assert len(unrelated.queries) > 1
    assert relevant.calls > 0
    assert timeline[0]["provider_asset_id"] == "relevant"
    assert timeline[0]["matched_search_query"] in relevant.queries


def test_unverified_license_is_rejected_without_downloading(tmp_path: Path) -> None:
    scene = deterministic_analysis(
        "Farmers use technology to monitor crops.", "documentary", [],
    ).scenes[0]
    unverified_candidate = _candidate("unknown-rights")
    unverified_candidate.rights_verified = False
    provider = MockImageProvider([unverified_candidate])
    timeline = plan_scene_timeline([scene], 4)
    paths, warnings, _ = retrieve_scene_images(
        [scene], timeline, tmp_path, _settings(tmp_path), {"unsplash": provider},
    )
    assert paths == [None]
    assert provider.downloaded == []
    assert timeline[0]["rights_rejections"] == ["Mock images:unknown-rights"]
    assert "rights-verified" in timeline[0]["selection_reason"]
    assert warnings


def test_irrelevant_candidates_are_rejected_for_scene_fallback(tmp_path: Path) -> None:
    scene = deterministic_analysis(
        "Satellite imagery monitors agricultural land.", "documentary", [],
    ).scenes[0]
    provider = MockImageProvider([_candidate("wrong", "colorful flowers in a garden")])
    timeline = plan_scene_timeline([scene], 4)
    paths, warnings, _ = retrieve_scene_images(
        [scene], timeline, tmp_path,
        _settings(tmp_path, image_min_relevance=0.8),
        {"unsplash": provider},
    )
    assert paths == [None]
    assert provider.downloaded == []
    assert timeline[0]["fallback_status"] == "fallback_card"
    assert warnings


def test_duplicate_source_is_not_assigned_twice_and_fallback_is_preserved(tmp_path: Path) -> None:
    analysis = deterministic_analysis(
        "Farmers use technology. Farmers monitor crops.", "documentary", [],
    )
    provider = MockImageProvider([_candidate("same")])
    timeline = plan_scene_timeline(analysis.scenes, 4)
    images, warnings, _ = retrieve_scene_images(
        analysis.scenes, timeline, tmp_path, _settings(tmp_path), {"unsplash": provider},
    )
    assert images[0] is not None
    assert images[1] is None
    assert [row["sequence_index"] for row in timeline] == [0, 1]
    assert timeline[1]["fallback_status"] == "fallback_card"
    assert warnings


def test_perceptually_identical_images_are_filtered(tmp_path: Path) -> None:
    analysis = deterministic_analysis("Farmers use technology. Farmers monitor crops.", "documentary", [])
    provider = MockImageProvider([_candidate("photo-one"), _candidate("photo-two")])
    timeline = plan_scene_timeline(analysis.scenes, 4)
    images, warnings, _ = retrieve_scene_images(
        analysis.scenes, timeline, tmp_path, _settings(tmp_path), {"unsplash": provider},
    )
    assert images[0] is not None
    assert images[1] is None
    assert warnings


def test_corrupt_top_candidate_tries_an_alternative(tmp_path: Path) -> None:
    scene = deterministic_analysis("Farmers use technology for crop production.", "documentary", []).scenes[0]
    provider = MockImageProvider(
        [_candidate("corrupt"), _candidate("alternative")], corrupt_ids={"corrupt"},
    )
    timeline = plan_scene_timeline([scene], 2)
    images, _, errors = retrieve_scene_images(
        [scene], timeline, tmp_path, _settings(tmp_path), {"unsplash": provider},
    )
    assert images[0] is not None
    assert timeline[0]["provider_asset_id"] == "alternative"
    assert errors


def test_timeline_duration_order_and_all_scenes_retained() -> None:
    scenes = deterministic_analysis("One idea. Another idea. Final idea.", "documentary", []).scenes
    timeline = plan_scene_timeline(scenes, 7.25)
    assert [row["sequence_index"] for row in timeline] == [0, 1, 2]
    assert len(timeline) == len(scenes)
    assert timeline[0]["start"] == 0
    assert timeline[-1]["end"] == pytest.approx(7.25)
    assert all(row["duration_seconds"] > 0 for row in timeline)
    assert all(timeline[index]["end"] == pytest.approx(timeline[index + 1]["start"])
               for index in range(len(timeline) - 1))


def test_fallback_card_and_aspect_profiles(tmp_path: Path) -> None:
    from incubrix.image_render import create_scene_frame
    for ratio in ("16:9", "9:16", "1:1"):
        image = create_scene_frame(None, "Narration must not appear on visual fallback",
                                   tmp_path / f"{ratio}.jpg", PROFILES[ratio], "farmer growing crops")
        with Image.open(image) as result:
            assert result.size == PROFILES[ratio]
    one = create_scene_frame(None, "first narration text", tmp_path / "one.jpg", (320, 180), "space satellite")
    two = create_scene_frame(None, "different narration words", tmp_path / "two.jpg", (320, 180), "space satellite")
    with Image.open(one) as left, Image.open(two) as right:
        assert list(left.getdata()) == list(right.getdata())


def test_image_render_final_audio_stream_and_duration(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    audio = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2",
        "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    scenes = [
        {"scene_id": "s1", "text": "First scene.", "start": 0, "end": 0.6},
        {"scene_id": "s2", "text": "Second scene.", "start": 0.6, "end": 1.2},
    ]
    result = render_image_video(
        [None, None], scenes, audio, tmp_path / "rendered.mp4", "16:9", fps=10,
        motion=False, work_dir=tmp_path / "render", landscape_size=(320, 180),
    )
    assert result["has_audio"] is True
    assert result["duration_verified"] is True
    assert result["duration"] == pytest.approx(1.2, abs=0.15)


def test_generated_image_zoom_pan_segment_renders_with_ffmpeg(tmp_path: Path) -> None:
    image = tmp_path / "generated.png"
    Image.new("RGB", (640, 360), (70, 130, 90)).save(image)
    audio = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.6", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    result = render_image_video(
        [image], [{"scene_id": "s", "sequence_index": 0, "text": "A scene.",
                   "start": 0, "end": 0.6}],
        audio, tmp_path / "motion.mp4", "16:9", fps=10, motion=True,
        work_dir=tmp_path / "motion_work", landscape_size=(320, 180),
    )
    assert result["has_audio"] is True
    assert result["duration_verified"] is True


@pytest.mark.parametrize(
    ("ratio", "dimensions"),
    [("16:9", (320, 180)), ("9:16", (180, 320)), ("1:1", (180, 180))],
)
def test_still_video_profile_dimensions(tmp_path: Path, ratio: str, dimensions: tuple[int, int]) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    audio = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
        "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    output = tmp_path / f"{ratio.replace(':', '_')}.mp4"
    scene = [{"scene_id": "s", "sequence_index": 0, "text": "A scene.", "start": 0, "end": 0.4}]
    render_image_video(
        [None], scene, audio, output, ratio, fps=10, motion=False,
        work_dir=tmp_path / f"work_{ratio.replace(':', '_')}", landscape_size=(320, 180),
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-select_streams", "v:0", "-of", "json", str(output)],
        check=True, capture_output=True, text=True,
    )
    dimensions_result = json.loads(probe.stdout)["streams"][0]
    assert (dimensions_result["width"], dimensions_result["height"]) == dimensions


def test_silent_narration_is_rejected(tmp_path: Path) -> None:
    audio = tmp_path / "silent.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "anullsrc=channel_layout=mono:sample_rate=48000:duration=0.4", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    scene = [{"scene_id": "s", "text": "Narration.", "start": 0, "end": 0.4}]
    with pytest.raises(FFmpegError, match="silent"):
        render_image_video([None], scene, audio, tmp_path / "silent.mp4", "16:9", fps=10,
                           motion=False, work_dir=tmp_path / "silent_work", landscape_size=(320, 180))


def test_video_clip_retrieval_fallback_and_mixed_render(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    video = tmp_path / "stock.mp4"
    audio = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "color=c=green:s=320x180:r=10:d=0.5", "-an", "-c:v", "libx264", str(video),
    ], check=True, capture_output=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True)
    scenes = deterministic_analysis("Farmers use technology for crop production.", "documentary", []).scenes
    candidate = Candidate(
        provider="Pixabay", source_id="clip-1", source_url="https://pixabay.com/videos/id-clip-1/",
        contributor="Contributor", license_name="Pixabay Content License",
        license_url="https://pixabay.com/service/license-summary/",
        download_url="https://cdn.example/clip.mp4", duration=1, width=320, height=180,
        tags="farmers crops technology", description="farmers using technology on crops",
        rights_verified=True,
    )

    class ClipProvider:
        name = "Pixabay"
        def search(self, query: str, target_ratio: str = "16:9") -> list[Candidate]:
            return [candidate]
        def download(self, item: Candidate, destination: Path) -> Path:
            shutil.copy2(video, destination)
            return destination

    scene_rows = plan_scene_timeline(scenes, 1)
    scene_rows[0]["aspect_ratio"] = "16:9"
    clip_paths, warnings, errors = retrieve_scene_video_clips(
        scenes, scene_rows, tmp_path / "clips", _settings(tmp_path),
        {"pixabay": ClipProvider()},
    )
    assert clip_paths[0] is not None
    assert scene_rows[0]["selected_visual"] == "stock_video"
    assert not warnings and not errors
    result = render_image_video(
        [None], scene_rows, audio, tmp_path / "mixed.mp4", "16:9", fps=10,
        motion=False, work_dir=tmp_path / "mixed_render", landscape_size=(320, 180),
        video_paths=clip_paths,
    )
    assert result["has_audio"]
    assert scene_rows[0]["rendered_visual"] == "stock_video"


def test_pexels_video_adapter_normalizes_results() -> None:
    class Response:
        status_code = 200
        def raise_for_status(self) -> None:
            pass
        def json(self) -> dict[str, object]:
            return {"videos": [{
                "id": 42, "url": "https://www.pexels.com/video/42/", "duration": 8,
                "user": {"name": "Creator", "url": "https://www.pexels.com/@creator"},
                "video_files": [
                    {"id": 1, "link": "https://videos.example/portrait.mp4", "width": 720, "height": 1280, "quality": "hd"},
                    {"id": 2, "link": "https://videos.example/landscape.mp4", "width": 1920, "height": 1080, "quality": "hd"},
                ],
            }]}
    class Session:
        def get(self, *args: object, **kwargs: object) -> Response:
            return Response()
    provider = PexelsVideoProvider("test-key", session=Session())
    results = provider.search("farmers", target_ratio="9:16")
    assert results[0].source_id == "42"
    assert results[0].contributor == "Creator"
    assert results[0].download_url.endswith("portrait.mp4")



def test_manifest_preserves_image_source_credit_and_fallback() -> None:
    timeline = make_timeline("job", [
        {"scene_id": "s1", "sequence_index": 0, "text": "Narration", "start": 0, "end": 2,
         "selected_image": "assets/s1.jpg", "provider": "Unsplash", "provider_asset_id": "photo-id",
         "source_url": "https://unsplash.com/photos/photo-id", "image_url": "https://images.unsplash.com/photo",
         "creator": "Photographer", "license": {"name": "Unsplash License"},
         "attribution_text": "Photo by Photographer on Unsplash", "candidate_score": 0.8},
        {"scene_id": "s2", "sequence_index": 1, "text": "Fallback", "start": 2, "end": 4,
         "selected_image": None, "fallback_status": "text_card", "fallback_asset": "assets/s2_fallback.jpg"},
    ], [])
    manifest = make_manifest("job", "model", ["Unsplash"], {}, ["16:9"], ["16:9"], {}, [], [], timeline)
    assert manifest["provenance"][0]["attribution_text"] == "Photo by Photographer on Unsplash"
    assert manifest["provenance"][1]["fallback_status"] == "text_card"


def test_complete_image_job_generates_manifest_and_ordered_video(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    config = _settings(tmp_path, video_width=320, video_height=180, video_fps=10, image_motion=False)
    store = JobStore(config.database_path)

    class FakeTTS:
        def synthesize(self, text: str, destination: Path, voice_settings: object) -> Path:
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1.2", "-c:a", "pcm_s16le", str(destination),
            ], check=True, capture_output=True)
            return destination

    class EmptyVideoProvider:
        name = "Wikimedia Commons"

        def search(self, query: str, page: int = 1) -> list[Candidate]:
            return []

    candidate = _candidate("licensed-farm-image")
    provider = MockImageProvider([candidate])
    processor = JobProcessor(
        config, store, tts=FakeTTS(), image_providers={"wikimedia": provider},
        wikimedia=EmptyVideoProvider(),
    )
    payload = {"request": {
        "script": "Farmers use technology. Farmers monitor crops.",
        "aspect_ratios": ["16:9"], "burn_captions": False, "include_srt": True,
    }}
    store.create_job("image-e2e", payload, None)
    processor.run("image-e2e", payload)
    record = store.get_job("image-e2e")
    assert record["status"] == "succeeded", record["error"]
    assert Path(record["outputs"]["video_16_9.mp4"]).is_file()
    timeline = json.loads(Path(record["outputs"]["timeline.json"]).read_text(encoding="utf-8"))
    manifest = json.loads(Path(record["outputs"]["manifest.json"]).read_text(encoding="utf-8"))
    assert [row["sequence_index"] for row in timeline["scenes"]] == [0, 1]
    assert timeline["scenes"][0]["selected_visual"] == "provider_image"
    assert timeline["scenes"][0]["license"]["rights_verified"] is True
    assert Path(config.output_dir, "image-e2e", timeline["scenes"][0]["selected_image"]).is_file()
    assert timeline["scenes"][0]["end"] == pytest.approx(timeline["scenes"][1]["start"])
    assert timeline["scenes"][-1]["render_status"] in {"rendered", "fallback_card_rendered"}
    assert manifest["input_type"] == "script"
    assert manifest["providers"] == ["Mock images"]
    assert "image_retrieval" in manifest["stage_timings_seconds"]
    assert manifest["outputs"]["paths"]["video_16_9.mp4"]
    assert "image-e2e" not in str(config.temp_dir / "another-job")


def test_image_job_completes_with_fallback_when_provider_fails(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    config = _settings(
        tmp_path,
        video_width=320,
        video_height=180,
        video_fps=10,
        image_motion=False,
        image_provider_order=("wikimedia",),
        video_provider_order=(),
    )
    store = JobStore(config.database_path)

    class FakeTTS:
        def synthesize(self, text: str, destination: Path, voice_settings: object) -> Path:
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1.2", "-c:a", "pcm_s16le", str(destination),
            ], check=True, capture_output=True)
            return destination

    class FailingImageProvider:
        name = "Failing image provider"

        def search(self, query: str, limit: int = 10) -> list[ImageCandidate]:
            raise ImageProviderError(self.name, "request timed out")

    class EmptyVideoProvider:
        name = "Wikimedia Commons"

        def search(self, query: str, page: int = 1) -> list[Candidate]:
            return []

    class GroqAssistedAnalyzer:
        def analyze(self, script: str, style: str, avoid: list[str]) -> tuple[object, str]:
            return deterministic_analysis(script, style, avoid), "groq_json"

    processor = JobProcessor(
        config, store, analyzer=GroqAssistedAnalyzer(), pixabay=False,
        wikimedia=EmptyVideoProvider(), tts=FakeTTS(),
        image_providers={"wikimedia": FailingImageProvider()},
    )
    payload = {"request": {
        "script": "Farmers use technology to improve crops.",
        "aspect_ratios": ["16:9"], "burn_captions": False, "include_srt": True,
    }}
    store.create_job("provider-failure-fallback", payload, None)
    processor.run("provider-failure-fallback", payload)

    record = store.get_job("provider-failure-fallback")
    assert record["status"] == "succeeded", record["error"]
    assert Path(record["outputs"]["video_16_9.mp4"]).is_file()
    timeline = json.loads(Path(record["outputs"]["timeline.json"]).read_text(encoding="utf-8"))
    scene = timeline["scenes"][0]
    assert scene["coverage_status"] == "fallback"
    assert scene["ai_generated"] is False
    assert scene["scene_analysis_method"] == "groq_json"
    assert "Failing image provider: request timed out" in " ".join(scene["retrieval_errors"])


def test_concurrent_retrieval_uses_isolated_job_paths(tmp_path: Path) -> None:
    scene_list = deterministic_analysis("Farmers use technology.", "documentary", []).scenes
    provider = MockImageProvider([_candidate("parallel")])
    results: list[Path | None] = []
    def work(job: str) -> None:
        paths, _, _ = retrieve_scene_images(
            scene_list, plan_scene_timeline(scene_list, 2), tmp_path / job,
            _settings(tmp_path / job), {"unsplash": provider},
        )
        results.append(paths[0])
    threads = [threading.Thread(target=work, args=(f"job-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert results[0] != results[1]
    assert all(path and path.is_file() for path in results)
