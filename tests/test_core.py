import pytest
import io, json
from unittest import mock

import musicbox_core as c


def test_normalize():
    assert c.normalize("  Beyoncé!! ") == "beyonce"
    assert c.normalize("A  B\tC") == "a b c"
    assert c.normalize("") == ""


def test_words_overlap():
    assert c.words_overlap("The Album", "album") == 1.0
    assert c.words_overlap("the a", "anything") == 0.0  # only stop words
    assert c.words_overlap("one two", "two three") == 0.5


def test_is_good_match():
    assert c.is_good_match("Björk", "Homogenic", "Bjork", "Homogenic")
    assert c.is_good_match("The Knife", "Silent Shout", "Knife", "Silent Shout")
    assert c.is_good_match("CCL & Ciel", "X", "Ciel & CCL", "X")
    assert c.is_good_match("Various Artists", "Kompakt Total", "Kompakt", "Total Kompakt")
    assert not c.is_good_match("Björk", "Homogenic", "Metallica", "Master of Puppets")
    assert not c.is_good_match("Björk", "Homogenic", "Björk", "Vespertine")


def _resp(payload):
    r = io.BytesIO(json.dumps(payload).encode())
    r.__enter__ = lambda s=r: r
    r.__exit__ = lambda *a: False
    return r


def test_fallback_order():
    calls = []

    def fake_search(q, a="", t="", verify=True, strict=False):
        calls.append(q)
        return (None, None)

    with mock.patch.object(c, "search_itunes_album", fake_search), \
         mock.patch.object(c, "search_artist_albums", lambda a, t, strict=False: ("u", "info")) as _:
        assert c.search_with_fallbacks("A&B", 'T"x"') == ("u", "info")
    assert calls == ["A&B T\"x\"", "A B Tx", "Tx"]


def test_fallback_stops_at_first_hit():
    seq = iter([(None, None), ("url2", "i2")])
    with mock.patch.object(c, "search_itunes_album", lambda *a, **k: next(seq)), \
         mock.patch.object(c, "search_artist_albums") as cat:
        assert c.search_with_fallbacks("a", "b") == ("url2", "i2")
        cat.assert_not_called()


def test_itunes_search_with_mocked_http():
    payload = {"results": [
        {"collectionViewUrl": "bad", "collectionName": "Zzz", "artistName": "Qqq"},
        {"collectionViewUrl": "good", "collectionName": "Homogenic", "artistName": "Björk"}]}
    with mock.patch("urllib.request.urlopen", return_value=_resp(payload)):
        assert c.search_itunes_album("q", "Bjork", "Homogenic") == ("good", "Björk — Homogenic")


def test_itunes_search_swallows_errors():
    with mock.patch("urllib.request.urlopen", side_effect=OSError):
        assert c.search_itunes_album("q", "a", "b") == (None, None)


def test_add_many_single_open_call():
    with mock.patch("subprocess.run") as run:
        c.add_many_to_musicbox(["https://a", "https://b"])
    run.assert_called_once()
    assert run.call_args[0][0] == ["open", "musicbox://add?url=https%3A%2F%2Fa%0Ahttps%3A%2F%2Fb"]


def test_outage_reads_as_not_found_for_the_cli_but_raises_when_strict():
    import urllib.error
    from unittest import mock

    import musicbox_core as core

    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        assert core.search_with_fallbacks("Artist", "Album") == (None, None)
        with pytest.raises(core.SearchUnavailable):
            core.search_with_fallbacks("Artist", "Album", strict=True)


def test_add_many_returns_the_open_process():
    from unittest import mock

    import musicbox_core as core

    with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
        assert core.add_many_to_musicbox(["u1", "u2"]).returncode == 0
    run.assert_called_once()
    assert run.call_args.args[0][1] == "musicbox://add?url=u1%0Au2"
