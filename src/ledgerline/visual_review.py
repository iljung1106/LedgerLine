# ruff: noqa: E501
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

from ledgerline.audio import resolve_ffmpeg
from ledgerline.compiler import compile_project


def create_visual_review(
    project: str | Path,
    *,
    audio: str | Path | None = None,
    ffmpeg: str | Path | None = None,
    musescore: str | Path | None = None,
    timeout: int = 180,
) -> dict:
    root = Path(project).resolve()
    build = root / "build"
    if not (build / "score.musicxml").is_file():
        compile_project(root)
    audio_path = Path(audio).resolve() if audio else build / "preview.wav"
    if not audio_path.is_file():
        raise ValueError("visual review requires --audio or build/preview.wav")
    review = build / "review"
    review.mkdir(parents=True, exist_ok=True)
    copied_audio = review / "preview.wav"
    if audio_path != copied_audio:
        shutil.copyfile(audio_path, copied_audio)
    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    waveform = review / "waveform.png"
    spectrogram = review / "spectrogram.png"
    _run(
        [
            str(ffmpeg_path),
            "-hide_banner",
            "-y",
            "-i",
            str(copied_audio),
            "-filter_complex",
            "showwavespic=s=1600x320:colors=0x55d6be|0xffd166:scale=sqrt",
            "-frames:v",
            "1",
            str(waveform),
        ],
        waveform,
        timeout,
    )
    _run(
        [
            str(ffmpeg_path),
            "-hide_banner",
            "-y",
            "-i",
            str(copied_audio),
            "-lavfi",
            "showspectrumpic=s=1600x640:legend=disabled:color=fiery:scale=log",
            "-frames:v",
            "1",
            str(spectrogram),
        ],
        spectrogram,
        timeout,
    )
    score_images: list[Path] = []
    if musescore:
        executable = Path(musescore).resolve(strict=True)
        target = review / "score.png"
        completed = subprocess.run(
            [str(executable), "-o", str(target), str(build / "score.musicxml")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"MuseScore score rendering failed: {completed.stderr[-2000:]}")
        score_images = sorted(review.glob("score*.png"))
    annotations = _annotations(build / "review-annotations.json")
    index = review / "index.html"
    index.write_text(_html(root.name, score_images, annotations), encoding="utf-8")
    report = {
        "schema_version": "1",
        "status": "ok",
        "index": str(index),
        "audio": str(copied_audio),
        "waveform": str(waveform),
        "spectrogram": str(spectrogram),
        "score_pages": [str(path) for path in score_images],
        "annotation_count": len(annotations),
    }
    (review / "review-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _run(command: list[str], output: Path, timeout: int) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0 or not output.is_file():
        raise ValueError(f"visual render failed: {completed.stderr[-2000:]}")


def _annotations(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw.get("annotations", []))


def _html(title: str, score_images: list[Path], annotations: list[dict]) -> str:
    score = "".join(
        f'<img class="score" src="{html.escape(path.name)}" alt="Score page {index + 1}">'
        for index, path in enumerate(score_images)
    )
    rows = "".join(
        "<button class='marker' data-seconds='{seconds}'><b>{anchor}</b> · {seconds:.3f}s — {text}</button>".format(
            seconds=float(item.get("seconds", item.get("start_seconds", 0.0))),
            anchor=html.escape(str(item.get("at", item.get("anchor", "?")))),
            text=html.escape(str(item.get("text", item.get("note", item.get("message", ""))))),
        )
        for item in annotations
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)} · LedgerLine review</title>
<style>
:root {{ color-scheme: dark; font-family: ui-sans-serif, system-ui; background:#0d1117; color:#e6edf3 }}
body {{ margin:0 auto; max-width:1680px; padding:32px }} h1 {{ font-weight:500; letter-spacing:-.03em }}
.panel {{ background:#161b22; border:1px solid #30363d; border-radius:14px; padding:18px; margin:18px 0 }}
audio,img {{ width:100% }} .score {{ margin:12px 0; background:white }}
.marker {{ display:block; width:100%; text-align:left; border:0; border-top:1px solid #30363d;
background:transparent; color:inherit; padding:12px; cursor:pointer }} .marker:hover {{ background:#21262d }}
</style></head><body><h1>{html.escape(title)}</h1>
<section class="panel"><audio id="audio" controls preload="metadata" src="preview.wav"></audio></section>
<section class="panel"><h2>Waveform</h2><img src="waveform.png" alt="Waveform"></section>
<section class="panel"><h2>Spectrum over time</h2><img src="spectrogram.png" alt="Spectrogram"></section>
<section class="panel"><h2>Listening markers</h2>{rows or "<p>No authored markers.</p>"}</section>
<section class="panel"><h2>Score</h2>{score or "<p>Pass --musescore to render score pages.</p>"}</section>
<script>const audio=document.querySelector('#audio'); document.querySelectorAll('.marker').forEach(b=>b.onclick=()=>{{audio.currentTime=Number(b.dataset.seconds);audio.play()}});</script>
</body></html>"""
