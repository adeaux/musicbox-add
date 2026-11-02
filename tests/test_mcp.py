import asyncio
from unittest import mock

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import musicbox_mcp as m

HIT = ("https://music.apple.com/x", "Artist — Album")


def call(args):
    return asyncio.run(m.mcp.call_tool("add_albums", args))


def text(result):
    content = result[0] if isinstance(result, tuple) else result
    return content[0].text


def albums(n=1):
    return [{"artist": f"A{i}", "album": f"B{i}"} for i in range(n)]


@pytest.fixture
def opened():
    with mock.patch.object(m, "_run_shortcut", return_value="An Album") as add, \
         mock.patch("subprocess.run") as run:
        yield add, run


def test_dry_run_default_opens_nothing(opened):
    add, run = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", side_effect=[HIT, (None, None)]):
        out = text(call({"albums": albums(2)}))
    assert out.startswith("Dry run, nothing added.")
    assert "A0 - B0" in out and "Not found" in out and "A1 - B1" in out
    add.assert_not_called()
    run.assert_not_called()


def test_real_run_saves_each_match(opened):
    add, _ = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", side_effect=[HIT, HIT, (None, None)]), \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        out = text(call({"albums": albums(3), "dry_run": False}))
    assert add.call_args_list == [mock.call(HIT[0]), mock.call(HIT[0])]
    assert out.startswith("Added 2 of 3 albums to MusicBox.") and "A2 - B2" in out


def test_real_run_no_matches_opens_nothing(opened):
    add, _ = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=(None, None)):
        out = text(call({"albums": albums(1), "dry_run": False}))
    add.assert_not_called()
    assert "Nothing to add" in out


@pytest.mark.parametrize("bad", ["false", 0, "no", None])
def test_non_boolean_dry_run_rejected(opened, bad):
    add, run = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=HIT) as s:
        with pytest.raises(Exception):
            call({"albums": albums(1), "dry_run": bad})
    s.assert_not_called()
    add.assert_not_called()


def test_no_gui_session(opened):
    add, _ = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=HIT), \
         mock.patch.object(m, "_gui_session_available", return_value=False):
        with pytest.raises(ToolError, match="nobody is logged in"):
            call({"albums": albums(1), "dry_run": False})
    add.assert_not_called()


def test_an_itunes_outage_is_an_error_not_a_miss(opened):
    """The real search runs; only the network is down."""
    import urllib.error
    add, _ = opened
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        with pytest.raises(ToolError, match="Couldn't reach the iTunes search service"):
            call({"albums": albums(1), "dry_run": False})
    add.assert_not_called()


def test_an_unexpected_search_failure_is_plain(opened):
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", side_effect=KeyError("x")):
        with pytest.raises(ToolError, match="failed unexpectedly"):
            call({"albums": albums(1)})


def test_every_save_failing_is_an_error(opened):
    add, _ = opened
    add.return_value = None
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=HIT), \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        with pytest.raises(ToolError, match="failed for every album"):
            call({"albums": albums(1), "dry_run": False})


def test_some_saves_failing_are_reported(opened):
    add, _ = opened
    add.side_effect = ["An Album", None]
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=HIT), \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        out = text(call({"albums": albums(2), "dry_run": False}))
    assert "Added 1 of 2" in out and "Failed to add (1)" in out and "A1 - B1" in out


def test_the_shortcut_gets_the_url_as_a_txt_file():
    def fake_run(cmd, **kwargs):
        source = cmd[cmd.index("--input-path") + 1]
        assert source.endswith(".txt") and open(source).read() == HIT[0]
        out = cmd[cmd.index("--output-path") + 1]
        open(out, "w").write("Some Album\n")
        return mock.Mock(returncode=0)

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert m._run_shortcut(HIT[0]) == "Some Album"
    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
        assert m._run_shortcut(HIT[0]) is None


def test_too_many_and_empty_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": albums(m.MAX_ALBUMS + 1)})
    with pytest.raises(ToolError):
        call({"albums": []})


def test_blank_strings_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": [{"artist": "  ", "album": "x"}]})
