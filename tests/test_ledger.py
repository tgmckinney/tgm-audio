from pathlib import Path

import pytest

from tgm_audio.ledger import Library


@pytest.fixture
def lib(tmp_path: Path) -> Library:
    lib = Library(tmp_path / "lib")
    lib.policy_path.write_text(
        '[licenses."epidemic:creator"]\nchannels = ["youtube:@mine"]\n'
        '[licenses.cc0]\nchannels = ["*"]\ncredit_required = true\n'
    )
    return lib


def test_add_dedupes_and_freezes(lib: Library, music: Path):
    a = lib.add(music, kind="music", source="own", license="cc0")
    b = lib.add(music, kind="music", source="own", license="cc0", title="renamed")
    assert a.id == b.id and len(lib.cues()) == 1
    assert lib.cues()[a.id].title == "renamed"
    assert lib.path(a).read_bytes() == music.read_bytes()


def test_check_gates_channel_licence_and_credit(lib: Library, music: Path, voice: Path):
    es = lib.add(music, kind="music", source="epidemic", license="epidemic:creator", title="Song",
                 credit="Music: Song (Epidemic Sound)")  # fmt: skip
    sfx = lib.add(voice, kind="sfx", source="freesound", license="cc0", title="Beep")
    lib.use(es.id, "inflation/short/a", "youtube:@mine")
    assert lib.check("inflation/short/a") == []
    assert lib.credits("inflation/short/a") == ["Music: Song (Epidemic Sound)"]
    lib.use(es.id, "inflation/short/b", "youtube:@other")
    lib.use(sfx.id, "inflation/short/b", "youtube:@other")
    problems = lib.check("inflation/short/b")
    assert any("does not cover youtube:@other" in p for p in problems)
    assert any("requires a credit" in p for p in problems)


def test_unknown_licence_blocks(lib: Library, music: Path):
    c = lib.add(music, kind="music", source="own", license="mystery")
    lib.use(c.id, "x", "youtube:@mine")
    assert "not in" in lib.check("x")[0]
