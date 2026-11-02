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
    with mock.patch.object(m.musicbox_core, "add_many_to_musicbox") as add, \
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


def test_real_run_one_open_call(opened):
    add, _ = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", side_effect=[HIT, HIT, (None, None)]), \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        out = text(call({"albums": albums(3), "dry_run": False}))
    add.assert_called_once_with([HIT[0], HIT[0]])
    assert "Sent 2 of 3" in out and "A2 - B2" in out


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


def test_network_error_is_plain(opened):
    import urllib.error
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks",
                           side_effect=urllib.error.URLError("boom")):
        with pytest.raises(ToolError, match="iTunes"):
            call({"albums": albums(1)})


def test_too_many_and_empty_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": albums(51)})
    with pytest.raises(ToolError):
        call({"albums": []})


def test_blank_strings_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": [{"artist": "  ", "album": "x"}]})
