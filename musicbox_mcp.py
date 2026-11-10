"""MCP server that saves music to MusicBox, with tags and notes. Runs over stdio.

Four tools:
- add_albums: saves albums, songs or mixes. Each item is either a link from any
  service (Apple Music, Spotify, Bandcamp, SoundCloud, YouTube, Discogs...) or an
  artist and album pair, which is looked up on Apple Music first.
- list_tags: the names of the tags already in MusicBox, so new adds reuse them.
- list_items: the items carrying one tag, each with its MusicBox id.
- delete_items: deletes the items carrying a tag (or just some of them, by id).
  Dry run by default.

MusicBox's own "Add Music" Shortcuts action saves with no dialog and takes a URL,
tags and notes, so a real run calls a small Shortcut once per item:
    shortcuts run "<MUSICBOX_SHORTCUT>" --input-path <file holding a JSON object>
The JSON is {"url": ..., "tags": [...], "notes": ...}. The Shortcut finds each tag
by name (creating it if it doesn't exist), passes everything to Add Music and
outputs the added music's title as plain text. MUSICBOX_SHORTCUT names it (default
"Duet: Add Tagged Music to MusicBox"). MUSICBOX_TAGS_SHORTCUT names the one that
lists tag names, one per line (default "Duet: List MusicBox Tags").

Listing and deleting use two more Shortcuts. MUSICBOX_LIST_SHORTCUT (default
"Duet: List MusicBox Items") takes {"tag": ...} and outputs one line per item,
"id<TAB>artist<TAB>title", or the text "no such tag". MUSICBOX_DELETE_SHORTCUT
(default "Duet: Delete MusicBox Items") takes {"ids": [...]}, finds each item by
id and runs MusicBox's Delete Music action; it outputs "deleted"."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, StrictBool

import musicbox_core

# Each save takes about 6 seconds, so a call stays a few minutes at most.
MAX_ITEMS = 20
MAX_TAGS = 10
MAX_TAG_LENGTH = 60
MAX_NOTES_LENGTH = 2000
MAX_URL_LENGTH = 2000
RUN_TIMEOUT_S = 30
SHORTCUT = os.environ.get("MUSICBOX_SHORTCUT", "Duet: Add Tagged Music to MusicBox")
TAGS_SHORTCUT = os.environ.get("MUSICBOX_TAGS_SHORTCUT", "Duet: List MusicBox Tags")
LIST_SHORTCUT = os.environ.get("MUSICBOX_LIST_SHORTCUT", "Duet: List MusicBox Items")
DELETE_SHORTCUT = os.environ.get("MUSICBOX_DELETE_SHORTCUT", "Duet: Delete MusicBox Items")
MAX_DELETE = 50
MAX_ID_LENGTH = 200
NO_SUCH_TAG = "no such tag"
SHORTCUTS_CLI = "/usr/bin/shortcuts"

mcp = FastMCP("musicbox")


class Album(BaseModel):
    """One thing to save: a link, or an artist and album to look up on Apple Music."""
    artist: str = ""
    album: str = ""
    url: str = ""
    tags: list[str] = []
    notes: str = ""


def _gui_session_available():
    """True if this user is logged in at the Mac's desktop."""
    try:
        console = subprocess.run(["stat", "-f", "%Su", "/dev/console"],
                                 capture_output=True, text=True, timeout=5)
        if console.returncode != 0 or console.stdout.strip() != _current_user():
            return False
        gui = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}"],
                             capture_output=True, text=True, timeout=5)
        return gui.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _current_user():
    import getpass
    return getpass.getuser()


def _shortcut(name, payload=None):
    """Run a Shortcut, optionally with text input. Returns its text output, or None if the run failed."""
    with tempfile.TemporaryDirectory() as tmp:
        # The .txt extension makes Shortcuts read the file's text, not the file itself.
        result = Path(tmp, "result.txt")
        cmd = [SHORTCUTS_CLI, "run", name]
        if payload is not None:
            source = Path(tmp, "input.txt")
            source.write_text(payload)
            cmd += ["--input-path", str(source)]
        cmd += ["--output-path", str(result), "--output-type", "public.plain-text"]
        try:
            # Without an input file, `shortcuts run` reads stdin. Here stdin is the MCP
            # connection, so leaving it open hangs the run and can eat protocol bytes.
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=RUN_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return result.read_text().strip() if result.exists() else ""


def _run_shortcut(url, tags, notes):
    """Save one item through the add Shortcut. Returns what it output, or None if the run failed."""
    return _shortcut(SHORTCUT, json.dumps({"url": url, "tags": tags, "notes": notes}))


def _existing_tags():
    """Tag names already in MusicBox, or None when they can't be read."""
    if not _gui_session_available():
        return None
    out = _shortcut(TAGS_SHORTCUT)
    if out is None:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def _clean_tags(raw):
    """Strip, check and de-duplicate tag names, keeping the first spelling."""
    tags, seen = [], set()
    for tag in raw:
        tag = " ".join(tag.split())
        if not tag:
            raise ToolError("Tag names can't be blank.")
        if len(tag) > MAX_TAG_LENGTH:
            raise ToolError(f"Tag names must be {MAX_TAG_LENGTH} characters or fewer.")
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise ToolError(f"Use at most {MAX_TAGS} tags per item.")
    return tags


def _clean_url(url):
    parts = urlsplit(url)
    if (parts.scheme not in ("http", "https") or not parts.netloc or len(url) > MAX_URL_LENGTH
            or any(c.isspace() for c in url)):
        raise ToolError("A url must be a single http or https link.")
    return url


def _label(item):
    if item["artist"] and item["album"]:
        return f"{item['artist']} - {item['album']}"
    return item["url"]


def _describe(matches, misses):
    lines = []
    if matches:
        lines.append(f"Matched {len(matches)}:")
        for m in matches:
            found = f" -> {m['matched']}" if m.get("matched") else ""
            tags = ", ".join(m["tags"]) or "none"
            link = "" if _label(m) == m["url"] else f" ({m['url']})"
            lines.append(f"  - {_label(m)}{found}{link} tags: {tags}")
    if misses:
        lines.append(f"Not found on Apple Music ({len(misses)}); send a link for these instead:")
        for m in misses:
            lines.append(f"  - {_label(m)}")
    return "\n".join(lines)


def _match_existing(items, existing):
    """Reuse MusicBox's own spelling of each tag; return the names that would be created."""
    by_fold = {t.casefold(): t for t in existing}
    new = []
    for item in items:
        item["tags"] = [by_fold.get(t.casefold(), t) for t in item["tags"]]
        new += [t for t in item["tags"] if t.casefold() not in by_fold and t not in new]
    return new


@mcp.tool()
def list_tags() -> str:
    """List the tag names already in MusicBox, one per line.

    Check these before adding, so new music reuses existing tags. Requires a
    logged-in desktop session on the Mac.
    """
    if not _gui_session_available():
        raise ToolError("MusicBox can't be reached: nobody is logged in to the Mac's desktop.")
    tags = _existing_tags()
    if tags is None:
        raise ToolError(f"Reading tags failed. Check that the '{TAGS_SHORTCUT}' shortcut exists on the Mac.")
    return "\n".join(tags) if tags else "MusicBox has no tags yet."


def _items_with_tag(tag):
    """Items carrying a tag as [{"id", "artist", "title"}], None when the list can't be read,
    or the string NO_SUCH_TAG when MusicBox has no tag by that name."""
    out = _shortcut(LIST_SHORTCUT, json.dumps({"tag": tag}))
    if out is None:
        return None
    if out.strip().casefold() == NO_SUCH_TAG:
        return NO_SUCH_TAG
    items = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0].strip():
            return None
        items.append({"id": parts[0].strip(), "artist": parts[1].strip(),
                      "title": "\t".join(parts[2:]).strip()})
    return items


def _read_items(tag):
    """Check the tag name and desktop session, then list the tag's items or raise a plain error."""
    tag = " ".join(tag.split())
    if not tag:
        raise ToolError("A tag name is required.")
    if len(tag) > MAX_TAG_LENGTH:
        raise ToolError(f"Tag names must be {MAX_TAG_LENGTH} characters or fewer.")
    if not _gui_session_available():
        raise ToolError("MusicBox can't be reached: nobody is logged in to the Mac's desktop.")
    items = _items_with_tag(tag)
    if items is None:
        raise ToolError(f"Reading items failed. Check that the '{LIST_SHORTCUT}' shortcut exists on the Mac.")
    if items == NO_SUCH_TAG:
        raise ToolError(f"MusicBox has no tag named '{tag}'. Check list_tags for the exact name.")
    return tag, items


def _item_lines(items):
    return "\n".join(f"  - {i['artist']} - {i['title']} (id {i['id']})" for i in items)


@mcp.tool()
def list_items(tag: str) -> str:
    """List the items in MusicBox that carry a tag, oldest added first.

    Each line shows artist, title and the item's MusicBox id. Use those ids with
    delete_items. Check list_tags for the exact tag name. Requires a logged-in desktop session on the Mac.
    """
    tag, items = _read_items(tag)
    if not items:
        return f"No items carry the tag '{tag}'."
    return f"{len(items)} item(s) tagged '{tag}':\n" + _item_lines(items)


@mcp.tool()
def delete_items(tag: str, ids: list[str] = [], dry_run: StrictBool = True) -> str:
    """Delete the items that carry a tag from MusicBox, or only some of them.

    Give the tag, and optionally ids (from list_items) to delete just those items;
    with no ids every item carrying the tag is deleted. Only items that carry the
    tag can be deleted, and the tag itself is left in place. At most 50 items per
    call.

    dry_run defaults to true: it only reports which items would be deleted. Pass a
    real boolean false to delete them; there is no undo, and the result lists what
    is still tagged afterwards. Requires a logged-in desktop session on the Mac.
    """
    tag, items = _read_items(tag)
    wanted = []
    for raw in ids:
        item_id = raw.strip()
        if (not item_id or len(item_id) > MAX_ID_LENGTH
                or any(c in item_id for c in "\t\r\n")):
            raise ToolError("Every id must be a non-empty single-line value from list_items.")
        if item_id not in wanted:
            wanted.append(item_id)
    by_id = {i["id"]: i for i in items}
    unknown = [w for w in wanted if w not in by_id]
    if unknown:
        raise ToolError(f"These ids don't carry the tag '{tag}', so nothing was deleted: "
                        + ", ".join(unknown))
    targets = [by_id[w] for w in wanted] if wanted else items
    if not targets:
        return f"Nothing to delete: no items carry the tag '{tag}'."
    if len(targets) > MAX_DELETE:
        raise ToolError(f"That would delete {len(targets)} items; the limit is {MAX_DELETE} per call. "
                        "Nothing was deleted. Pass ids to delete in batches.")
    if dry_run:
        return (f"Dry run, nothing deleted. Would delete {len(targets)} of {len(items)} "
                f"item(s) tagged '{tag}':\n" + _item_lines(targets))

    out = _shortcut(DELETE_SHORTCUT, json.dumps({"ids": [t["id"] for t in targets]}))
    if out is None:
        raise ToolError(f"Deleting failed. Check that the '{DELETE_SHORTCUT}' shortcut exists on the Mac. "
                        "Run list_items to see what is left.")
    after = _items_with_tag(tag)
    if not isinstance(after, list):
        return (f"Asked MusicBox to delete {len(targets)} item(s), but couldn't re-read the tag to "
                "confirm. Run list_items to check.\n" + _item_lines(targets))
    left = {i["id"] for i in after}
    gone = [t for t in targets if t["id"] not in left]
    stuck = [t for t in targets if t["id"] in left]
    head = f"Deleted {len(gone)} of {len(targets)} item(s) tagged '{tag}'."
    if gone:
        head += "\n" + _item_lines(gone)
    if stuck:
        head += f"\nStill in MusicBox ({len(stuck)}):\n" + _item_lines(stuck)
    return head + f"\n{len(after)} item(s) still carry '{tag}'."


@mcp.tool()
def add_albums(albums: list[Album], tags: list[str] = [], dry_run: StrictBool = True) -> str:
    """Save albums, songs or mixes to MusicBox, tagged.

    Each item is either {url} (a link from any service: Apple Music, Spotify,
    Bandcamp, SoundCloud, YouTube, Discogs and so on, saved as is) or {artist,
    album} (looked up on Apple Music). Either kind can carry its own tags and
    notes. Top-level tags are added to every item. Every item needs at least one
    tag to be saved; tags that don't exist yet are created, so check list_tags
    first and reuse existing names.

    dry_run defaults to true: it only reports what would be added and which tags
    would be new. Pass a real boolean false to save; each item is added with no
    dialog. At most 20 items per call. Requires a logged-in desktop session on
    the Mac.
    """
    if not 1 <= len(albums) <= MAX_ITEMS:
        raise ToolError(f"Send between 1 and {MAX_ITEMS} items.")
    shared = _clean_tags(tags)
    cleaned = []
    for a in albums:
        artist, album, url = a.artist.strip(), a.album.strip(), a.url.strip()
        if url:
            url = _clean_url(url)
        elif not artist or not album:
            raise ToolError("Every item needs a url, or a non-empty artist and album name.")
        notes = a.notes.strip()
        if len(notes) > MAX_NOTES_LENGTH:
            raise ToolError(f"Notes must be {MAX_NOTES_LENGTH} characters or fewer.")
        cleaned.append({"artist": artist, "album": album, "url": url,
                        "tags": _clean_tags(shared + a.tags), "notes": notes})

    matches, misses = [], []
    try:
        for item in cleaned:
            if item["url"]:
                matches.append(item)
                continue
            url, info = musicbox_core.search_with_fallbacks(item["artist"], item["album"], strict=True)
            if url:
                matches.append({**item, "matched": info, "url": url})
            else:
                misses.append(item)
    except musicbox_core.SearchUnavailable:
        raise ToolError("Couldn't reach the iTunes search service, so nothing was looked up. Try again later.")
    except Exception:
        raise ToolError("Searching for the albums failed unexpectedly. Try again later.")

    untagged = [m for m in matches if not m["tags"]]
    existing = _existing_tags() if matches else None
    new_tags = _match_existing(matches, existing) if existing is not None else None
    if new_tags:
        tag_line = "New tags that would be created: " + ", ".join(new_tags)
    elif new_tags is None:
        tag_line = "Couldn't read MusicBox's tags, so new ones aren't flagged."
    else:
        tag_line = "All tags already exist in MusicBox."

    if dry_run:
        out = "Dry run, nothing added.\n" + _describe(matches, misses)
        if matches:
            out += "\n" + tag_line
        if untagged:
            out += f"\n{len(untagged)} item(s) have no tags and would be refused."
        return out

    if not matches:
        return "Nothing to add: no albums were found on Apple Music.\n" + _describe(matches, misses)
    if untagged:
        raise ToolError("Every item needs at least one tag. Nothing was added:\n"
                        + "\n".join(f"  - {_label(m)}" for m in untagged))
    if not _gui_session_available():
        raise ToolError("MusicBox can't be reached: nobody is logged in to the Mac's desktop.")

    added, failed = [], []
    for m in matches:
        (failed if _run_shortcut(m["url"], m["tags"], m["notes"]) is None else added).append(m)
    if not added:
        raise ToolError(
            f"Adding to MusicBox failed for every item. Check that the '{SHORTCUT}' "
            "shortcut exists on the Mac and that MusicBox is installed."
        )
    head = f"Added {len(added)} of {len(cleaned)} items to MusicBox."
    if failed:
        head += f"\nFailed to add ({len(failed)}):\n" + "\n".join(
            f"  - {_label(m)} ({m['url']})" for m in failed)
    if new_tags:
        head += "\nCreated new tags: " + ", ".join(new_tags)
    return head + "\n" + _describe(added, misses)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
