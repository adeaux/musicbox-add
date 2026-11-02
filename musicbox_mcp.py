"""MCP server exposing one tool, add_albums, that finds albums on Apple Music
and saves them to MusicBox. Runs over stdio.

MusicBox's own "Add Music" Shortcuts action saves an album with no dialog, so a
real run calls a one-action Shortcut once per album:
    shortcuts run "<MUSICBOX_SHORTCUT>" --input-path <file with the album URL>
The Shortcut takes the URL as input, passes it to Add Music and outputs the added
music (its title comes back as plain text). MUSICBOX_SHORTCUT names it (default
"Duet: Add Music to MusicBox")."""

import os
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, StrictBool

import musicbox_core

# Each save takes about 6 seconds, so a call stays a few minutes at most.
MAX_ALBUMS = 20
RUN_TIMEOUT_S = 30
SHORTCUT = os.environ.get("MUSICBOX_SHORTCUT", "Duet: Add Music to MusicBox")
SHORTCUTS_CLI = "/usr/bin/shortcuts"

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


def _run_shortcut(url):
    """Save one album through the Shortcut. Returns what it output, or None if the run failed."""
    with tempfile.TemporaryDirectory() as tmp:
        # The .txt extension makes Shortcuts read the file's text, not the file itself.
        source, result = Path(tmp, "album.txt"), Path(tmp, "result.txt")
        source.write_text(url)
        try:
            proc = subprocess.run(
                [SHORTCUTS_CLI, "run", SHORTCUT, "--input-path", str(source),
                 "--output-path", str(result), "--output-type", "public.plain-text"],
                capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return result.read_text().strip() if result.exists() else ""


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
    """Find albums on Apple Music and save them to MusicBox.

    dry_run defaults to true: it only reports what would be added. Pass a real
    boolean false to save the matches to MusicBox; each is added with no dialog.
    At most 20 albums per call. Requires a logged-in desktop session on the Mac.
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
            url, info = musicbox_core.search_with_fallbacks(artist, album, strict=True)
            entry = {"artist": artist, "album": album}
            if url:
                matches.append({**entry, "matched": info, "url": url})
            else:
                misses.append(entry)
    except musicbox_core.SearchUnavailable:
        raise ToolError("Couldn't reach the iTunes search service, so nothing was looked up. Try again later.")
    except Exception:
        raise ToolError("Searching for the albums failed unexpectedly. Try again later.")

    if dry_run:
        return "Dry run, nothing added.\n" + _describe(matches, misses)

    if not matches:
        return "Nothing to add: no albums were found on Apple Music.\n" + _describe(matches, misses)
    if not _gui_session_available():
        raise ToolError("MusicBox can't be reached: nobody is logged in to the Mac's desktop.")

    added, failed = [], []
    for m in matches:
        (failed if _run_shortcut(m["url"]) is None else added).append(m)
    if not added:
        raise ToolError(
            f"Adding to MusicBox failed for every album. Check that the '{SHORTCUT}' "
            "shortcut exists on the Mac and that MusicBox is installed."
        )
    head = f"Added {len(added)} of {len(cleaned)} albums to MusicBox."
    if failed:
        head += f"\nFailed to add ({len(failed)}):\n" + "\n".join(
            f"  - {m['artist']} - {m['album']} ({m['url']})" for m in failed)
    return head + "\n" + _describe(added, misses)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
