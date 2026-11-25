"""Where the data lives, and the atomic write every saved file goes through."""

from palmcards import paths


def test_the_data_folder_follows_the_environment_then_the_checkout_then_app_support(tmp_path, monkeypatch):
    monkeypatch.setenv("PALMCARDS_DATA", str(tmp_path / "mine"))
    assert paths.data_dir() == (tmp_path / "mine").resolve()
    monkeypatch.delenv("PALMCARDS_DATA")
    monkeypatch.setattr(paths, "CHECKOUT", tmp_path)
    assert paths.data_dir() == paths.APP_SUPPORT  # no sessions/ in the checkout
    (tmp_path / "sessions").mkdir()
    assert paths.data_dir() == tmp_path / "sessions"


def test_write_atomic_leaves_the_new_content_and_no_temp_file(tmp_path):
    target = tmp_path / "a.json"
    target.write_bytes(b"old")
    paths.write_atomic(target, b'{"x": 1}\n')
    assert target.read_bytes() == b'{"x": 1}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["a.json"]
