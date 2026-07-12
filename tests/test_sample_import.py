from __future__ import annotations

from ledgerline.sample_import import inspect_sample_library


def test_sfz_audit_reads_inheritance_zones_loops_round_robin_and_missing(tmp_path) -> None:
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "one.wav").write_bytes(b"RIFF")
    sfz = tmp_path / "instrument.sfz"
    sfz.write_text(
        """
<control> default_path=samples/
<global> lovel=1 hivel=100
<group> seq_length=2 loop_mode=loop_continuous
<region> sample=one.wav key=C4 seq_position=1 loop_start=10 loop_end=200
<region> sample=missing.wav lokey=C#4 hikey=D4 seq_position=2
""".strip(),
        encoding="utf-8",
    )
    report = inspect_sample_library(sfz)
    assert report["format"] == "sfz"
    assert len(report["regions"]) == 2
    assert report["regions"][0]["pitch_keycenter"] == 60
    assert report["regions"][0]["loop_start"] == 10
    assert report["round_robin_groups"] == [2]
    assert report["missing_samples"] == 1
