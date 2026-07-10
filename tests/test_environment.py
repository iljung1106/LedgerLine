import ledgerline.environment as environment
from ledgerline.environment import doctor
from ledgerline.setup_plan import create_setup_plan


def test_doctor_is_machine_readable() -> None:
    report = doctor()
    assert report["schema_version"] == "1"
    assert report["status"] in {"ok", "degraded"}
    assert report["capabilities"]["compile_musicxml"] is True
    for renderer in report["renderers"]:
        assert len(renderer["sha256"]) == 64


def test_setup_plan_blocks_unreleased_core_pack() -> None:
    plan = create_setup_plan(["core"])
    assert plan["status"] == "blocked"
    assert plan["steps"] == []
    assert plan["blocked"][0]["pack"] == "core"


def test_doctor_reports_actionable_missing_audio(monkeypatch) -> None:
    monkeypatch.setattr(environment, "_find_fluidsynth", lambda: None)
    monkeypatch.setattr(environment, "_find_soundfonts", lambda: [])
    monkeypatch.setattr(environment, "_find_executable", lambda *args: None)
    report = environment.doctor()
    assert report["status"] == "degraded"
    assert {item["code"] for item in report["problems"]} == {
        "FLUIDSYNTH_NOT_FOUND",
        "SOUNDFONT_NOT_FOUND",
    }
