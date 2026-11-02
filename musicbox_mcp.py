"""MCP server that saves music to MusicBox, with tags and notes. Runs over stdio.

Two tools:
- add_albums: saves albums, songs or mixes. Each item is either a link from any
  service (Apple Music, Spotify, Bandcamp, SoundCloud, YouTube, Discogs...) or an
  artist and album pair, which is looked up on Apple Music first.
- list_tags: the names of the tags already in MusicBox, so new adds reuse them.

MusicBox's own "Add Music" Shortcuts action saves with no dialog and takes a URL,
tags and notes, so a real run calls a small Shortcut once per item:
    shortcuts run "<MUSICBOX_SHORTCUT>" --input-path <file holding a JSON object>
The JSON is {"url": ..., "tags": [...], "notes": ...}. The Shortcut finds each tag
by name (creating it if it doesn't exist), passes everything to Add Music and
outputs the added music's title as plain text. MUSICBOX_SHORTCUT names it (default
"Duet: Add Tagged Music to MusicBox"). MUSICBOX_TAGS_SHORTCUT names the one that
lists tag names, one per line (default "Duet: List MusicBox Tags")."""

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
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RUN_TIMEOUT_S)
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
            lines.append(f"  - {_label(m)}{found} ({m['url']}) tags: {tags}")
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
