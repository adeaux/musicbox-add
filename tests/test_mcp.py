import asyncio
import json
import subprocess
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


def albums(n=1, tags=("techno",)):
    return [{"artist": f"A{i}", "album": f"B{i}", "tags": list(tags)} for i in range(n)]


@pytest.fixture
def opened():
    with mock.patch.object(m, "_run_shortcut", return_value="An Album") as add, \
         mock.patch.object(m, "_existing_tags", return_value=["Techno", "Ambient"]), \
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
    assert add.call_args_list == [mock.call(HIT[0], ["Techno"], ""), mock.call(HIT[0], ["Techno"], "")]
    assert out.startswith("Added 2 of 3 items to MusicBox.") and "A2 - B2" in out


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
        with pytest.raises(ToolError, match="failed for every item"):
            call({"albums": albums(1), "dry_run": False})


def test_some_saves_failing_are_reported(opened):
    add, _ = opened
    add.side_effect = ["An Album", None]
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks", return_value=HIT), \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        out = text(call({"albums": albums(2), "dry_run": False}))
    assert "Added 1 of 2" in out and "Failed to add (1)" in out and "A1 - B1" in out


def test_the_shortcut_gets_url_tags_and_notes_as_json_in_a_txt_file():
    def fake_run(cmd, **kwargs):
        assert cmd[2] == m.SHORTCUT
        source = cmd[cmd.index("--input-path") + 1]
        assert source.endswith(".txt")
        assert json.loads(open(source).read()) == {"url": HIT[0], "tags": ["Techno"], "notes": "n"}
        out = cmd[cmd.index("--output-path") + 1]
        open(out, "w").write("Some Album\n")
        return mock.Mock(returncode=0)

    with mock.patch("subprocess.run", side_effect=fake_run):
        assert m._run_shortcut(HIT[0], ["Techno"], "n") == "Some Album"
    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
        assert m._run_shortcut(HIT[0], ["Techno"], "n") is None


def test_too_many_and_empty_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": albums(m.MAX_ITEMS + 1)})
    with pytest.raises(ToolError):
        call({"albums": []})


def test_blank_strings_rejected(opened):
    with pytest.raises(ToolError):
        call({"albums": [{"artist": "  ", "album": "x"}]})
    with pytest.raises(ToolError, match="blank"):
        call({"albums": albums(1, tags=["  "])})


LINK = "https://soundcloud.com/someone/a-mix"


def test_a_link_is_saved_as_is_without_searching(opened):
    add, _ = opened
    with mock.patch.object(m.musicbox_core, "search_with_fallbacks") as s, \
         mock.patch.object(m, "_gui_session_available", return_value=True):
        out = text(call({"albums": [{"url": LINK, "tags": ["mixes"], "notes": "1988 tape"}],
                         "dry_run": False}))
    s.assert_not_called()
    add.assert_called_once_with(LINK, ["mixes"], "1988 tape")
    assert out.startswith("Added 1 of 1 items") and "Created new tags: mixes" in out


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///etc/passwd", "soundcloud.com/x",
                                 "https://a.com/x\nhttps://b.com/y", "musicbox://add?url=x"])
def test_only_single_http_links_are_accepted(opened, bad):
    add, _ = opened
    with pytest.raises(ToolError, match="http or https"):
        call({"albums": [{"url": bad, "tags": ["x"]}], "dry_run": False})
    add.assert_not_called()


def test_shared_tags_merge_with_item_tags_and_reuse_existing_spelling(opened):
    add, _ = opened
    with mock.patch.object(m, "_gui_session_available", return_value=True):
        call({"albums": [{"url": LINK, "tags": ["AMBIENT", "detroit"]}],
              "tags": ["techno", "Detroit"], "dry_run": False})
    add.assert_called_once_with(LINK, ["Techno", "Detroit", "Ambient"], "")


def test_untagged_items_are_refused_on_a_real_run(opened):
    add, _ = opened
    with mock.patch.object(m, "_gui_session_available", return_value=True):
        with pytest.raises(ToolError, match="at least one tag"):
            call({"albums": [{"url": LINK}, {"url": LINK, "tags": ["x"]}], "dry_run": False})
    add.assert_not_called()


def test_dry_run_flags_new_and_missing_tags(opened):
    out = text(call({"albums": [{"url": LINK, "tags": ["techno", "mixes"]}, {"url": LINK}]}))
    assert f"  - {LINK} tags: Techno, mixes" in out
    assert "New tags that would be created: mixes" in out
    assert "1 item(s) have no tags" in out


def test_dry_run_says_when_tags_cant_be_read(opened):
    with mock.patch.object(m, "_existing_tags", return_value=None):
        out = text(call({"albums": [{"url": LINK, "tags": ["x"]}]}))
    assert "Couldn't read MusicBox's tags" in out


def test_too_many_tags_rejected(opened):
    with pytest.raises(ToolError, match="at most"):
        call({"albums": albums(1, tags=[f"t{i}" for i in range(m.MAX_TAGS + 1)])})


def test_list_tags():
    with mock.patch.object(m, "_gui_session_available", return_value=True), \
         mock.patch.object(m, "_shortcut", return_value="Techno\n\nAmbient\n") as sc:
        assert text(asyncio.run(m.mcp.call_tool("list_tags", {}))) == "Techno\nAmbient"
    sc.assert_called_once_with(m.TAGS_SHORTCUT)
    with mock.patch.object(m, "_gui_session_available", return_value=True), \
         mock.patch.object(m, "_shortcut", return_value=None):
        with pytest.raises(ToolError, match="Reading tags failed"):
            asyncio.run(m.mcp.call_tool("list_tags", {}))


@pytest.mark.parametrize("payload", [None, "{}"])
def test_shortcuts_never_read_the_mcp_connection(payload):
    """`shortcuts run` reads stdin when given no input file; stdin is the MCP pipe."""
    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        m._shortcut(m.TAGS_SHORTCUT, payload)
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def call_tool(name, args):
    return text(asyncio.run(m.mcp.call_tool(name, args)))


ITEMS = [{"id": "ID1", "artist": "Artist One", "title": "Title One"},
         {"id": "ID2", "artist": "Artist Two", "title": "Title Two"}]


@pytest.fixture
def tagged():
    with mock.patch.object(m, "_gui_session_available", return_value=True), \
         mock.patch.object(m, "_items_with_tag", return_value=list(ITEMS)) as items, \
         mock.patch.object(m, "_shortcut", return_value="deleted") as sc:
        yield items, sc


def test_list_items_shows_ids(tagged):
    out = call_tool("list_items", {"tag": "Oh My Rockness"})
    assert "2 item(s) tagged 'Oh My Rockness'" in out and "Artist One - Title One (id ID1)" in out


def test_list_items_parses_the_shortcut_output():
    with mock.patch.object(m, "_shortcut", return_value="ID1\tA\tT\n\nID2\tB\tC\tD\n"):
        assert m._items_with_tag("x") == [{"id": "ID1", "artist": "A", "title": "T"},
                                          {"id": "ID2", "artist": "B", "title": "C\tD"}]
    with mock.patch.object(m, "_shortcut", return_value="No such tag"):
        assert m._items_with_tag("x") == m.NO_SUCH_TAG
    with mock.patch.object(m, "_shortcut", return_value=""):
        assert m._items_with_tag("x") == []
    with mock.patch.object(m, "_shortcut", return_value="garbage line"):
        assert m._items_with_tag("x") is None
    with mock.patch.object(m, "_shortcut", return_value=None):
        assert m._items_with_tag("x") is None


def test_list_items_errors_are_plain():
    with mock.patch.object(m, "_gui_session_available", return_value=False):
        with pytest.raises(ToolError, match="nobody is logged in"):
            call_tool("list_items", {"tag": "x"})
    with mock.patch.object(m, "_gui_session_available", return_value=True):
        with mock.patch.object(m, "_items_with_tag", return_value=m.NO_SUCH_TAG):
            with pytest.raises(ToolError, match="no tag named 'x'"):
                call_tool("list_items", {"tag": "x"})
        with mock.patch.object(m, "_items_with_tag", return_value=None):
            with pytest.raises(ToolError, match="shortcut exists"):
                call_tool("list_items", {"tag": "x"})
        with pytest.raises(ToolError, match="tag name is required"):
            call_tool("list_items", {"tag": "  "})


def test_delete_dry_run_is_the_default_and_deletes_nothing(tagged):
    _, sc = tagged
    out = call_tool("delete_items", {"tag": "t"})
    assert out.startswith("Dry run, nothing deleted. Would delete 2 of 2")
    sc.assert_not_called()


@pytest.mark.parametrize("bad", ["false", "False", 0, None, "no"])
def test_delete_needs_a_real_boolean_false(tagged, bad):
    _, sc = tagged
    with pytest.raises(Exception):
        call_tool("delete_items", {"tag": "t", "dry_run": bad})
    sc.assert_not_called()


def test_delete_real_run_deletes_the_requested_ids_and_checks(tagged):
    items, sc = tagged
    items.side_effect = [list(ITEMS), [ITEMS[1]]]
    out = call_tool("delete_items", {"tag": "t", "ids": ["ID1"], "dry_run": False})
    name, payload = sc.call_args.args
    assert name == m.DELETE_SHORTCUT and json.loads(payload) == {"ids": ["ID1"]}
    assert out.startswith("Deleted 1 of 1 item(s) tagged 't'.") and "1 item(s) still carry 't'" in out


def test_delete_without_ids_targets_the_whole_tag(tagged):
    items, sc = tagged
    items.side_effect = [list(ITEMS), []]
    out = call_tool("delete_items", {"tag": "t", "dry_run": False})
    assert json.loads(sc.call_args.args[1]) == {"ids": ["ID1", "ID2"]}
    assert "Deleted 2 of 2" in out


def test_delete_reports_items_that_survive(tagged):
    items, _ = tagged
    items.side_effect = [list(ITEMS), list(ITEMS)]
    out = call_tool("delete_items", {"tag": "t", "ids": ["ID1"], "dry_run": False})
    assert out.startswith("Deleted 0 of 1") and "Still in MusicBox (1)" in out


def test_delete_refuses_ids_outside_the_tag(tagged):
    _, sc = tagged
    with pytest.raises(ToolError, match="NOPE"):
        call_tool("delete_items", {"tag": "t", "ids": ["ID1", "NOPE"], "dry_run": False})
    sc.assert_not_called()


@pytest.mark.parametrize("bad", ["", "  ", "a\tb", "a\nb", "x" * 201])
def test_delete_refuses_bad_ids(tagged, bad):
    _, sc = tagged
    with pytest.raises(ToolError, match="Every id"):
        call_tool("delete_items", {"tag": "t", "ids": [bad], "dry_run": False})
    sc.assert_not_called()


def test_delete_refuses_more_than_the_limit(tagged):
    items, sc = tagged
    items.return_value = [{"id": f"I{n}", "artist": "a", "title": "t"} for n in range(m.MAX_DELETE + 1)]
    with pytest.raises(ToolError, match="limit is 50"):
        call_tool("delete_items", {"tag": "t", "dry_run": False})
    sc.assert_not_called()


def test_delete_failure_is_plain(tagged):
    items, sc = tagged
    sc.return_value = None
    with pytest.raises(ToolError, match="Deleting failed"):
        call_tool("delete_items", {"tag": "t", "dry_run": False})


def test_delete_with_no_items_does_nothing(tagged):
    items, sc = tagged
    items.return_value = []
    assert call_tool("delete_items", {"tag": "t", "dry_run": False}).startswith("Nothing to delete")
    sc.assert_not_called()


@pytest.fixture
def tags():
    with mock.patch.object(m, "_gui_session_available", return_value=True), \
         mock.patch.object(m, "_existing_tags", return_value=["Techno", "duet test tag"]) as ex, \
         mock.patch.object(m, "_items_with_tag", return_value=list(ITEMS)), \
         mock.patch.object(m, "_shortcut", return_value="deleted") as sc:
        yield ex, sc


def test_delete_tag_dry_run_is_the_default(tags):
    _, sc = tags
    out = call_tool("delete_tag", {"tag": "Techno"})
    assert out == "Dry run, nothing deleted. Would delete the tag 'Techno'; 2 item(s) carry it."
    sc.assert_not_called()


@pytest.mark.parametrize("bad", ["false", "False", 0, None, "no"])
def test_delete_tag_needs_a_real_boolean_false(tags, bad):
    _, sc = tags
    with pytest.raises(Exception):
        call_tool("delete_tag", {"tag": "Techno", "dry_run": bad})
    sc.assert_not_called()


def test_delete_tag_real_run_deletes_and_rereads(tags):
    ex, sc = tags
    ex.side_effect = [["Techno", "duet test tag"], ["duet test tag"]]
    out = call_tool("delete_tag", {"tag": "Techno", "dry_run": False})
    name, payload = sc.call_args.args
    assert name == m.DELETE_TAG_SHORTCUT and json.loads(payload) == {"tag": "Techno"}
    assert out.startswith("Deleted the tag 'Techno'")


def test_delete_tag_needs_the_exact_name(tags):
    _, sc = tags
    with pytest.raises(ToolError, match="Did you mean 'Techno'"):
        call_tool("delete_tag", {"tag": "techno", "dry_run": False})
    with pytest.raises(ToolError, match="no tag named 'Tech'"):
        call_tool("delete_tag", {"tag": "Tech", "dry_run": False})
    sc.assert_not_called()


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61])
def test_delete_tag_refuses_bad_names(tags, bad):
    _, sc = tags
    with pytest.raises(ToolError):
        call_tool("delete_tag", {"tag": bad, "dry_run": False})
    sc.assert_not_called()


def test_delete_tag_reports_a_tag_that_survives(tags):
    ex, _ = tags
    ex.side_effect = [["Techno"], ["Techno"]]
    out = call_tool("delete_tag", {"tag": "Techno", "dry_run": False})
    assert "still in MusicBox" in out


def test_delete_tag_failures_are_plain(tags):
    ex, sc = tags
    sc.return_value = None
    with pytest.raises(ToolError, match="Deleting the tag failed"):
        call_tool("delete_tag", {"tag": "Techno", "dry_run": False})
    ex.side_effect = None
    ex.return_value = None
    with pytest.raises(ToolError, match="Reading tags failed"):
        call_tool("delete_tag", {"tag": "Techno"})
    with mock.patch.object(m, "_gui_session_available", return_value=False):
        with pytest.raises(ToolError, match="nobody is logged in"):
            call_tool("delete_tag", {"tag": "Techno"})


def test_delete_tag_dry_run_copes_with_an_uncountable_tag(tags):
    with mock.patch.object(m, "_items_with_tag", return_value=None):
        assert "couldn't count" in call_tool("delete_tag", {"tag": "Techno"})
