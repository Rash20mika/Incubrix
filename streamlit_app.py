from __future__ import annotations

import copy
import html
import json
import os
import time
import uuid
from pathlib import Path

import requests
import streamlit as st

from incubrix.recorder import recorder_html
from incubrix.timeline_editor import customize_timeline

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
st.set_page_config(page_title="IncuBrix Video Studio", page_icon="🎬", layout="wide")
st.markdown("""
<style>
.block-container {max-width: 1180px; padding-top: 2rem;}
.hero {padding: 1.5rem 2rem; border-radius: 16px; background: linear-gradient(120deg,#152944,#266578); color:white;}
.muted {color:#64748b;}
</style>
""", unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>IncuBrix Video Studio</h1><p>Turn a script or narration into a sourced b-roll video.</p></div>', unsafe_allow_html=True)
st.caption("Scenes are timed automatically to the narration. Review provider attribution and item-specific reuse terms before publishing.")


def render_timeline_editor(job_id: str, timeline: dict[str, object]) -> None:
    scenes = timeline.get("scenes", [])
    if not isinstance(scenes, list) or not scenes:
        return
    duration = max(float(scene.get("end", 0)) for scene in scenes)
    if duration <= 0:
        st.warning("Timeline duration is unavailable; the editor cannot be opened.")
        return
    editor_key = f"timeline_draft_{job_id}"
    source_key = f"timeline_source_{job_id}"
    if source_key not in st.session_state:
        st.session_state[source_key] = copy.deepcopy(scenes)
    if editor_key not in st.session_state:
        st.session_state[editor_key] = copy.deepcopy(scenes)
    draft = st.session_state[editor_key]
    source = st.session_state[source_key]
    for scene in draft:
        scene_id = str(scene["scene_id"])
        for field_name in ("start", "end"):
            state_key = f"timeline_{field_name}_{job_id}_{scene_id}"
            if state_key in st.session_state:
                scene[field_name] = float(st.session_state[state_key])
        visual_key = f"timeline_visual_{job_id}_{scene_id}"
        if visual_key in st.session_state:
            scene["selected_visual"] = st.session_state[visual_key]

    st.subheader("Timeline Editor")
    st.caption(
        f"Audio duration: {duration:.2f}s. Timeline edits change visual order and timing only. "
        "The retained narration and its original caption timing are never trimmed, retimed, or regenerated."
    )
    mode_key = f"timeline_mode_{job_id}"
    if mode_key not in st.session_state:
        initial_mode = str(timeline.get("timing_mode", "automatic"))
        st.session_state[mode_key] = initial_mode if initial_mode in {
            "automatic", "equal", "manual", "narration_synced",
        } else "automatic"
    mode = st.selectbox(
        "Timing mode",
        ["automatic", "equal", "manual", "narration_synced"],
        format_func=lambda value: {
            "automatic": "Automatic — preserve estimated/timestamp-derived scene lengths",
            "equal": "Equal duration — divide the full narration evenly",
            "manual": "Manual — set contiguous scene start/end times",
            "narration_synced": "Narration-synced — retain original spoken-scene order and timing",
        }[value],
        key=mode_key,
    )

    asset_labels: dict[str, str] = {"": "Fallback visual"}
    for source_scene in source:
        for key, label in (("selected_image", "Image"), ("selected_clip", "Video clip"),
                           ("fallback_asset", "Fallback card")):
            asset = source_scene.get(key)
            if asset:
                asset_path = str(asset).replace("\\", "/")
                creator = source_scene.get("creator") or source_scene.get("contributor") or "creator unknown"
                asset_labels[asset_path] = (
                    f'{source_scene["scene_id"]} · {label} · {creator}'
                )

    with st.expander("Add a visual-only scene", expanded=False):
        st.caption("This adds a visual segment without changing or regenerating the narration.")
        if st.button(
            "Add scene", key=f"timeline_add_{job_id}",
            disabled=mode == "narration_synced",
        ):
            draft.append({
                "scene_id": f"manual-{uuid.uuid4().hex[:8]}",
                "sequence_index": len(draft),
                "text": "",
                "narration_text": "",
                "core_meaning": "User-added visual scene",
                "visual_description": "User-added visual scene",
                "start": 0.0,
                "end": min(duration, 2.0),
                "duration_seconds": min(duration, 2.0),
                "selected_image": None,
                "selected_clip": None,
                "selection_reason": "Manually added visual scene; no narration was added.",
                "timing_method": "manual",
            })
            st.session_state[editor_key] = draft
            st.rerun()

    edited_rows: list[dict[str, object]] = []
    for index, scene in enumerate(draft):
        scene_id = str(scene["scene_id"])
        st.markdown(f"**{index + 1}. {scene_id}** — {scene.get('text') or 'Visual-only scene'}")
        st.caption(
            f"Current: {float(scene.get('start', 0)):.2f}s → "
            f"{float(scene.get('end', 0)):.2f}s "
            f"({float(scene.get('duration_seconds', 0)):.2f}s)"
        )
        columns = st.columns([1, 1, 1.5, 0.45, 0.45, 0.55])
        start = columns[0].number_input(
            "Start (s)", min_value=0.0, max_value=duration, step=0.1,
            value=min(duration, max(0.0, float(scene.get("start", 0)))),
            key=f"timeline_start_{job_id}_{scene_id}",
            disabled=mode != "manual",
        )
        end = columns[1].number_input(
            "End (s)", min_value=0.0, max_value=duration, step=0.1,
            value=min(duration, max(0.0, float(scene.get("end", duration)))),
            key=f"timeline_end_{job_id}_{scene_id}",
            disabled=mode != "manual",
        )
        current_visual = next(
            (str(scene.get(key)).replace("\\", "/") for key in
             ("selected_visual", "selected_image", "selected_clip", "fallback_asset") if scene.get(key)),
            "",
        )
        options = list(asset_labels)
        if current_visual not in asset_labels:
            asset_labels[current_visual] = "Current asset"
            options.append(current_visual)
        visual = columns[2].selectbox(
            "Selected visual",
            options,
            index=options.index(current_visual),
            format_func=lambda value: asset_labels[value],
            key=f"timeline_visual_{job_id}_{scene_id}",
        )
        if columns[3].button(
            "↑", key=f"timeline_up_{job_id}_{scene_id}",
            disabled=index == 0 or mode == "narration_synced",
        ):
            draft[index - 1], draft[index] = draft[index], draft[index - 1]
            st.session_state[editor_key] = draft
            st.rerun()
        if columns[4].button(
            "↓", key=f"timeline_down_{job_id}_{scene_id}",
            disabled=index == len(draft) - 1 or mode == "narration_synced",
        ):
            draft[index + 1], draft[index] = draft[index], draft[index + 1]
            st.session_state[editor_key] = draft
            st.rerun()
        if columns[5].button(
            "Remove", key=f"timeline_remove_{job_id}_{scene_id}",
            disabled=mode == "narration_synced",
        ):
            draft.pop(index)
            st.session_state[editor_key] = draft
            st.rerun()
        row = dict(scene)
        row["sequence_index"] = index
        row["start"], row["end"] = float(start), float(end)
        row["duration_seconds"] = max(0.0, float(end) - float(start))
        row["selected_visual"] = visual
        draft[index].update({
            "sequence_index": index,
            "start": row["start"],
            "end": row["end"],
            "duration_seconds": row["duration_seconds"],
            "selected_visual": visual,
        })
        edited_rows.append(row)
        if visual:
            asset_name = visual.removeprefix("assets/")
            preview_url = f"{API_URL}/api/v1/jobs/{job_id}/files/assets/{asset_name}"
            if Path(visual).suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}:
                st.video(preview_url, alt=f"Selected video clip for {scene_id}")
            else:
                st.image(preview_url, alt=f"Selected visual for {scene_id}")
        else:
            st.info("No stock visual selected — a fallback card will be rendered.")
        source_url = str(scene.get("source_url", ""))
        if source_url.startswith("https://"):
            license_data = scene.get("license") or {}
            st.markdown(
                f'[Source: {html.escape(str(scene.get("provider") or scene.get("provider_id") or "Media provider"))}]'
                f'({html.escape(source_url, quote=True)})'
            )
            st.caption(
                f'Licence metadata: {license_data.get("name", "not available")}; '
                f'relevance score: {scene.get("candidate_score", scene.get("relevance_score", "n/a"))}.'
            )
        for retrieval_error in scene.get("retrieval_errors", []):
            st.warning(str(retrieval_error))
        st.divider()

    if not edited_rows:
        st.error("Keep at least one scene in the visual timeline.")
        return
    st.markdown("**Timeline preview**")
    try:
        preview = customize_timeline(
            source, edited_rows, duration, mode, int(os.getenv("VIDEO_FPS", "30")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        st.error(f"Timeline is not renderable yet: {exc}")
        preview = []
    audio_bar = (
        '<div style="height:12px;background:#4f8bd6;border-radius:6px;margin:5px 0 12px">'
        '</div><small>Audio / narration — full duration</small>'
    )
    st.markdown("Audio track")
    st.markdown(audio_bar, unsafe_allow_html=True)
    if preview:
        segments = []
        for scene in preview:
            left = max(0.0, float(scene["start"])) / duration * 100
            width = max(0.5, float(scene["duration_seconds"]) / duration * 100)
            visual = scene.get("selected_visual") or next(
                (scene.get(key) for key in ("selected_image", "selected_clip", "fallback_asset")
                 if scene.get(key)),
                None,
            )
            label = "Fallback" if not visual else str(scene["scene_id"])
            segments.append(
                f'<div title="{html.escape(label)}: {float(scene["start"]):.2f}-'
                f'{float(scene["end"]):.2f}s" style="position:absolute;left:{left:.4f}%;'
                f'width:{width:.4f}%;height:26px;overflow:hidden;white-space:nowrap;'
                f'background:{"#8994a3" if not visual else "#3a9d83"};color:white;'
                'font-size:11px;padding:4px;box-sizing:border-box;border-right:1px solid white">'
                f'{html.escape(label)}</div>'
            )
        st.markdown(
            '<small>Visual track</small><div style="position:relative;height:28px;'
            'background:#263345;border-radius:5px;margin:5px 0">'
            + "".join(segments) + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Video duration: {float(preview[-1]['end']):.2f}s · "
            f"Narration duration: {duration:.2f}s"
        )
    if st.button(
        "Save timeline and rerender", type="primary", key=f"timeline_render_{job_id}",
        disabled=not preview,
    ):
        try:
            with st.spinner("Rendering the customized timeline with the original narration..."):
                response = requests.post(
                    f"{API_URL}/api/v1/jobs/{job_id}/timeline/render",
                    json={"mode": mode, "scenes": edited_rows},
                    timeout=900,
                )
            if not response.ok:
                st.error(response.json().get("error", {}).get("message", response.text))
            else:
                rendered_scenes = response.json()["timeline"]["scenes"]
                st.session_state[editor_key] = rendered_scenes
                st.session_state[source_key] = copy.deepcopy(rendered_scenes)
                st.success("Updated timeline rendered successfully.")
                st.rerun()
        except requests.RequestException as exc:
            st.error(f"Could not rerender the customized timeline: {exc}")


mode = st.radio(
    "Primary input",
    ["Write a script", "Upload narration", "Record narration"],
    horizontal=True,
)
max_recording_seconds = 600
if mode != "Write a script":
    try:
        readiness = requests.get(f"{API_URL}/ready", timeout=5).json()
        max_recording_seconds = int(readiness.get("limits", {}).get("max_audio_minutes", 10)) * 60
        if not readiness.get("capabilities", {}).get("audio_transcription", False):
            st.warning(
                "Audio transcription is not configured. Add GROQ_API_KEY to the backend .env file "
                "and restart the API before submitting recorded or uploaded narration."
            )
    except requests.RequestException:
        st.info("The backend connection could not be checked. Audio submissions will show a specific error if unavailable.")

audio = None
recording_token = None
if mode == "Record narration":
    recording_token = st.session_state.setdefault("recording_token", uuid.uuid4().hex)
    recorder_url = f"{API_URL.rstrip('/')}/api/v1/recordings/{recording_token}"
    st.html(recorder_html(recorder_url, max_seconds=max_recording_seconds), unsafe_allow_javascript=True)
    st.caption("If microphone permission is denied, allow it in your browser’s site settings and refresh this page.")

with st.form("job_form"):
    script = ""
    if mode == "Write a script":
        script = st.text_area("Script", height=180, placeholder="Paste your narration. Keep the original wording; scenes are segmented automatically.")
        voice_id = st.text_input("Optional local TTS voice ID")
        rate = st.slider("Narration speed", 120, 240, 175)
    elif mode == "Upload narration":
        audio = st.file_uploader(
            "Narration audio (MP3, WAV, M4A, WEBM, OGG, MP4)",
            type=["mp3", "wav", "m4a", "webm", "ogg", "mp4"],
        )
        voice_id, rate = "", 175
    else:
        voice_id, rate = "", 175
    scene_text = st.text_area("Optional scene hints (one scene per line: narration | visual hint)", height=90)
    left, right = st.columns(2)
    with left:
        style = st.selectbox("Visual style", ["documentary", "cinematic", "clean corporate", "educational", "warm and human"])
        ratios = st.multiselect("Aspect ratios", ["16:9", "9:16", "1:1"], default=["16:9"])
    with right:
        avoid = st.text_input("Avoid content (comma separated)", placeholder="logos, graphic imagery")
        burn = st.checkbox("Burn captions into video", False)
        include_srt = st.checkbox("Include downloadable SRT", True)
        rights = st.checkbox("I confirm I have reuse rights for clips in local_footage")
    submitted = st.form_submit_button("Generate video", type="primary", width="stretch")

recorded_audio: bytes | None = None
recording_error: str | None = None
recording_extension = ".webm"
recording_mime_type = "audio/webm"
if submitted and mode == "Record narration" and recording_token:
    try:
        recording_response = requests.get(
            f"{API_URL}/api/v1/recordings/{recording_token}", timeout=30,
        )
        if recording_response.status_code == 200:
            recorded_audio = recording_response.content
            recording_extension = recording_response.headers.get("X-Audio-Extension", ".webm")
            recording_mime_type = recording_response.headers.get("content-type", "audio/webm")
        else:
            try:
                recording_error = recording_response.json().get("error", {}).get("message")
            except ValueError:
                recording_error = None
            recording_error = recording_error or "Finish and save a voice recording before generating the video."
    except requests.RequestException as exc:
        recording_error = f"Could not retrieve the recording: {exc}"

if submitted:
    if not ratios:
        st.error("Select at least one aspect ratio.")
    elif mode == "Write a script" and not script.strip():
        st.error("Enter a script before generating.")
    elif mode == "Upload narration" and audio is None:
        st.error("Upload an audio file or record your narration before generating.")
    elif mode == "Record narration" and recorded_audio is None:
        st.error(recording_error or "Finish and save a voice recording before generating the video.")
    else:
        hints = []
        for line in scene_text.splitlines():
            parts = line.split("|", 1)
            if parts[0].strip():
                hints.append({"text": parts[0].strip(), "visual_hint": parts[1].strip() if len(parts) > 1 else ""})
        form_data = {
            "style": style, "avoid_content": json.dumps([x.strip() for x in avoid.split(",") if x.strip()]),
            "aspect_ratios": json.dumps(ratios), "burn_captions": str(burn).lower(),
            "include_srt": str(include_srt).lower(), "scene_list": json.dumps(hints),
            "confirm_local_rights": str(rights).lower(),
            "voice_settings": json.dumps({"voice_id": voice_id, "rate": rate}),
            "timeline_mode": "automatic",
            "prepare_only": "false",
        }
        if recorded_audio is not None:
            files = {"audio": (f"voice_recording{recording_extension}", recorded_audio, recording_mime_type)}
        elif audio:
            files = {"audio": (audio.name, audio.getvalue(), audio.type or "application/octet-stream")}
        else:
            files = None
        if script.strip():
            form_data["script"] = script
        try:
            response = requests.post(f"{API_URL}/api/v1/broll/jobs", data=form_data, files=files, timeout=60)
            if response.status_code != 202:
                st.error(response.json().get("error", {}).get("message", response.text))
            else:
                st.session_state.job_id = response.json()["job_id"]
                if recording_token:
                    cleanup = requests.delete(
                        f"{API_URL}/api/v1/recordings/{recording_token}", timeout=10,
                    )
                    if not cleanup.ok:
                        st.warning("The video job started, but the temporary recording could not be cleaned up.")
                    st.session_state.recording_token = uuid.uuid4().hex
        except requests.RequestException as exc:
            st.error(f"Could not connect to the backend: {exc}")

job_id = st.session_state.get("job_id")
if job_id:
    try:
        status_response = requests.get(f"{API_URL}/api/v1/jobs/{job_id}", timeout=10)
        status_response.raise_for_status()
        job = status_response.json()
        st.subheader(f"Job {job_id[:8]}")
        st.progress(job["progress"], text=f'{job["stage"].replace("_", " ").title()} — {job["status"]}')
        if job["status"] == "failed":
            st.error(job.get("error", {}).get("message", "Job failed"))
        elif job["status"] == "awaiting_timeline":
            st.warning(
                "This job was created by an earlier version and is waiting for a manual timeline review. "
                "Submit the script again to generate the video automatically."
            )
        elif job["status"] == "succeeded":
            result = requests.get(f"{API_URL}/api/v1/jobs/{job_id}/result", timeout=10).json()
            outputs = result["outputs"]
            timeline = requests.get(f"{API_URL}/api/v1/jobs/{job_id}/timeline", timeout=10).json()
            for scene in timeline["scenes"]:
                status = scene.get("coverage_status", scene.get("retrieval_status", "unknown"))
                with st.expander(f'{scene["scene_id"]} · {status} · {scene["text"][:100]}'):
                    visual_path = scene.get("selected_image") or scene.get("fallback_asset")
                    if visual_path:
                        asset_name = visual_path.replace("\\", "/").removeprefix("assets/")
                        asset_response = requests.get(
                            f"{API_URL}/api/v1/jobs/{job_id}/files/assets/{asset_name}", timeout=30,
                        )
                        if asset_response.ok:
                            st.image(asset_response.content, width="stretch", alt=f"Selected visual for {scene['scene_id']}")
                    elif scene.get("selected_clip"):
                        asset_name = scene["selected_clip"].replace("\\", "/").removeprefix("assets/")
                        st.video(f"{API_URL}/api/v1/jobs/{job_id}/files/assets/{asset_name}")
                    st.write(scene.get("selection_reason", ""))
                    if scene.get("source_url"):
                        st.markdown(f'[Source: {scene.get("provider") or scene.get("provider_id")} · {scene.get("creator") or scene.get("contributor") or "creator not provided"}]({scene["source_url"]})')
                        st.caption(f'Licence metadata: {scene.get("license", {}).get("name", "not available")}. Check source terms; not independently verified.')
                    if scene.get("candidate_score") is not None:
                        st.caption(f'Relevance score: {scene["candidate_score"]} ({scene.get("score_method", "estimated")})')
                    if scene.get("attribution_text"):
                        st.info(scene["attribution_text"])
                    for issue in scene.get("retrieval_errors", []):
                        st.warning(issue)
                    for issue in scene.get("warnings", []):
                        st.warning(issue)
            for name, url in outputs.items():
                if name.startswith("video_") and name.endswith(".mp4"):
                    st.subheader(f"Preview {name.removeprefix('video_').removesuffix('.mp4').replace('_', ':')}")
                    st.video(f"{API_URL}{url}")
                    st.download_button(f"Download {name}", requests.get(f"{API_URL}{url}", timeout=120).content,
                                       file_name=name, mime="video/mp4")
            st.warning("\n".join(timeline.get("warnings", [])) or "No pipeline warnings.")
            for name, url in outputs.items():
                if name.endswith(".srt") or name.endswith("manifest.json"):
                    file_response = requests.get(f"{API_URL}{url}", timeout=30)
                    st.download_button(f"Download {name}", file_response.content, file_name=name)
        else:
            time.sleep(2)
            st.rerun()
    except requests.RequestException as exc:
        st.error(f"Unable to retrieve job status: {exc}")
