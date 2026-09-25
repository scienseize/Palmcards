import json
from datetime import datetime

import numpy as np

from palmcards.session import Session, read_wav


def test_add_take_writes_wav_and_json(tmp_path):
    log = tmp_path / "gesture-logs" / "20260925-120000.jsonl"
    session = Session.create("samples/talk.md", root=tmp_path, gesture_log=log, now=datetime(2026, 9, 25, 12, 0, 0))
    assert not session.dir.exists()  # nothing on disk until the first take

    rate = 16000
    audio = (0.5 * np.sin(np.linspace(0, 2 * np.pi * 440, rate * 2))).astype(np.float32)
    take = session.add_take(audio, rate, t_start=12.3456, started=datetime(2026, 9, 25, 12, 0, 5),
                            sections=[(0.0, 0), (1.25, 1)])

    assert session.dir == tmp_path / "20260925-120000-talk"
    assert (take.number, take.wav, take.duration_s, take.t_start) == (1, "take-01.wav", 2.0, 12.346)
    assert take.peak > 0.49 and not take.silent
    back, back_rate = read_wav(session.dir / "take-01.wav")
    assert back_rate == rate and np.allclose(back, audio, atol=1e-4)

    data = json.loads((session.dir / "session.json").read_text())
    assert data["gesture_log"] == "gesture-logs/20260925-120000.jsonl"
    assert data["takes"][0]["sections"] == [{"section": 0, "t": 0.0}, {"section": 1, "t": 1.25}]


def test_takes_number_up_and_reload(tmp_path):
    session = Session.create("notes.txt", root=tmp_path)
    for _ in range(2):
        session.add_take(np.zeros(800, np.float32), 8000, 0.0, datetime.now(), [(0.0, 0)])
    loaded = Session.load(session.dir)
    assert [t.wav for t in loaded.takes] == ["take-01.wav", "take-02.wav"]
    assert loaded.takes[1].silent  # all zeros: the mic delivered nothing
