"""MCP server exposing one tool, add_albums, that finds albums on Apple Music
and opens them in MusicBox. Runs over stdio."""

import os
import subprocess

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, StrictBool

import musicbox_core

MAX_ALBUMS = 50

mcp = FastMCP("musicbox")


class Album(BaseModel):
    artist: str
    album: str


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


def _describe(matches, misses):
    lines = []
    if matches:
        lines.append(f"Matched {len(matches)}:")
        for m in matches:
            lines.append(f"  - {m['artist']} - {m['album']} -> {m['matched']} ({m['url']})")
    if misses:
        lines.append(f"Not found on Apple Music ({len(misses)}):")
        for m in misses:
            lines.append(f"  - {m['artist']} - {m['album']}")
    return "\n".join(lines)


@mcp.tool()
def add_albums(albums: list[Album], dry_run: StrictBool = True) -> str:
    """Find albums on Apple Music and add them to MusicBox.

    dry_run defaults to true: it only reports what would be added. Pass a real
    boolean false to open the albums in MusicBox (one dialog). Requires a
    logged-in desktop session on the Mac.
    """
    if not 1 <= len(albums) <= MAX_ALBUMS:
        raise ToolError(f"Send between 1 and {MAX_ALBUMS} albums.")
    cleaned = []
    for a in albums:
        artist, album = a.artist.strip(), a.album.strip()
        if not artist or not album:
            raise ToolError("Every album needs a non-empty artist and album name.")
        cleaned.append((artist, album))

    matches, misses = [], []
    try:
        for artist, album in cleaned:
            url, info = musicbox_core.search_with_fallbacks(artist, album)
            entry = {"artist": artist, "album": album}
            if url:
                matches.append({**entry, "matched": info, "url": url})
            else:
                misses.append(entry)
    except ToolError:
        raise
    except Exception:
        raise ToolError("Couldn't reach the iTunes search service. Try again later.")

    if dry_run:
        return "Dry run, nothing added.\n" + _describe(matches, misses)

    if matches:
        if not _gui_session_available():
            raise ToolError("MusicBox can't be opened: nobody is logged in to the Mac's desktop.")
        try:
            musicbox_core.add_many_to_musicbox([m["url"] for m in matches])
        except Exception:
            raise ToolError("Couldn't open MusicBox. Is it installed?")
        head = f"Sent {len(matches)} of {len(cleaned)} to MusicBox."
    else:
        head = "Nothing to add: no albums were found on Apple Music."
    return head + "\n" + _describe(matches, misses)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
