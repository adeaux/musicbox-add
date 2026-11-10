# musicbox-add

Extract albums from a music article and add them to [MusicBox](https://apps.apple.com/app/musicbox-save-music-for-later/id1614730313) in one click.

Open a "best of" list (like [RA's Best Records of 2024](https://ra.co/features/4404)) in Safari, run the script, and it extracts every album, finds them on Apple Music, and opens them all in a single MusicBox dialog.

## How it works

1. Reads the current Safari tab's rendered page text via AppleScript
2. Sends it to Claude (Haiku) to extract artist/album pairs
3. Searches the iTunes API for each album with match verification
4. Opens all found albums in MusicBox via `musicbox://add` URL scheme — one dialog for everything

## Requirements

- macOS
- Python 3 (pre-installed on macOS)
- [MusicBox](https://apps.apple.com/app/musicbox-save-music-for-later/id1614730313)
- [Anthropic API key](https://console.anthropic.com/)

## Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/musicbox-add.git
cd musicbox-add

# Make it executable
chmod +x musicbox-add

# Symlink to home directory (or anywhere in your PATH)
ln -sf "$(pwd)/musicbox-add" ~/musicbox-add

# Store your API key in the config file the script reads by default
mkdir -p ~/.config/musicbox
echo "sk-ant-..." > ~/.config/musicbox/api_key
chmod 600 ~/.config/musicbox/api_key
```

The key file keeps `ANTHROPIC_API_KEY` out of your shell profile, so tools that switch behavior when that variable is set (like Claude Code's auth) are unaffected. Setting the environment variable still works and takes precedence over the file.

## Usage

```bash
# Open a music article in Safari, then:
~/musicbox-add

# Dry run — show what would be added without adding:
~/musicbox-add --dry-run

# Fetch a URL directly (works for non-JS-rendered sites):
~/musicbox-add https://example.com/best-albums-2024
```

## MCP server

`musicbox-mcp` is a stdio [MCP](https://modelcontextprotocol.io) server with five tools. It does not read Safari or call Anthropic, so it needs no API key.

### `add_albums(albums, tags=[], dry_run=true)`

Saves up to 20 items to MusicBox with no dialog. Each item is one of:

- `{"url": "..."}`: a link from any service MusicBox understands (Apple Music, Spotify, Bandcamp, SoundCloud, YouTube, Tidal, Qobuz, Discogs and so on), saved as is. Only single `http` or `https` links are accepted.
- `{"artist": "...", "album": "..."}`: looked up on Apple Music with the same matching as the CLI. Misses are reported; send a link for those instead.

Either kind can carry its own `tags` (up to 10) and `notes`. Top-level `tags` are added to every item. Tag names are compared without regard to case, and an existing tag's spelling is reused, so `techno` lands on your `Techno` tag. Tags that don't exist yet are created.

- `dry_run` defaults to true and reports matches, misses, each item's tags, and which tags would be new. Only a real boolean `false` performs the add.
- A real run refuses the whole call if any item has no tags, so nothing goes in untagged.
- Adding needs a logged-in desktop session on the Mac; otherwise the tool returns an error and saves nothing.

### `list_tags()`

Returns the tag names already in MusicBox, one per line, so a client can reuse them instead of inventing near-duplicates.

### `list_items(tag)`

Lists the items carrying one tag, oldest added first, each as artist, title and MusicBox id. The ids are what `delete_items` takes. It's read-only. An unknown tag is an error that points to `list_tags`.

### `delete_items(tag, ids=[], dry_run=true)`

Deletes items from MusicBox. Give the `tag`; with no `ids` every item carrying it is deleted, and with `ids` (from `list_items`) only those. An id that doesn't carry the tag refuses the whole call, so a typo can't reach an unrelated item. The tag itself is kept. At most 50 items per call.

- `dry_run` defaults to true and lists exactly what would go. Only a real boolean `false` deletes (the string `"false"` is refused).
- After a real delete the tool lists the tag again and reports what is gone and anything that survived.
- There is no undo. Deleting a tag has its own tool, `delete_tag`.
- Needs a logged-in desktop session on the Mac.

### `delete_tag(tag, dry_run=true)`

Deletes one tag, by its exact name. The name must match one from `list_tags` letter for letter (a different capitalisation is refused, with a hint), and only one tag goes per call.

- `dry_run` defaults to true and reports the exact tag and how many items carry it. Only a real boolean `false` deletes.
- After a real delete the tool re-reads the tag list and says whether the tag is gone.
- There is no undo. Needs a logged-in desktop session on the Mac.

### The five Shortcuts

Saving goes through MusicBox's own Shortcuts actions, which work without the Import dialog that the `musicbox://add` URL scheme opens. The server runs each Shortcut with `shortcuts run`, about 6 seconds per item. It closes the command's stdin: given no input file, `shortcuts run` waits on stdin, which inside the server is the MCP connection. To try the tag list by hand, run `shortcuts run "Duet: List MusicBox Tags" < /dev/null`.

**Duet: Add Tagged Music to MusicBox** (or set `MUSICBOX_SHORTCUT`). The server passes it a text file holding `{"url": "...", "tags": ["..."], "notes": "..."}`.

1. Receive text input. Add **Get Dictionary from Input**.
2. **Get Dictionary Value** `tags` and set it to a variable, then **Repeat with Each** item in it:
   - MusicBox **Find Tag** where Name is Repeat Item, limit 1.
   - **If** the result has no value, MusicBox **Create Tag** named Repeat Item; otherwise use the found tag.
   - **Add to Variable** `Tags`.
3. **Get Dictionary Value** `url` and `notes`.
4. MusicBox **Add Music** with the URL, Tags set to the `Tags` variable, and Notes.
5. **Stop and Output** the result of Add Music.

**Duet: List MusicBox Tags** (or set `MUSICBOX_TAGS_SHORTCUT`): MusicBox **Find Tag** with no filter, sorted by Name, **Get Name** of each, **Combine Text** with new lines, **Stop and Output**.

**Duet: List MusicBox Items** (or set `MUSICBOX_LIST_SHORTCUT`). Input is a text file holding `{"tag": "..."}`. MusicBox **Find Tag** where Name is the tag (limit 1); if there is none, **Stop and Output** the text `no such tag`. Otherwise **Find Music** where Tags contain it, sorted by Date Added, and output one line per item, `id<TAB>artist<TAB>title`.

**Duet: Delete MusicBox Items** (or set `MUSICBOX_DELETE_SHORTCUT`). Input is `{"ids": ["..."]}`. For each id, **Find Music** where Identifier is the id (limit 1) and **Add to Variable**; then MusicBox **Delete Music** on the variable (with Show When Run off) and **Stop and Output** `deleted`. 

**Duet: Delete MusicBox Tag** (or set `MUSICBOX_DELETE_TAG_SHORTCUT`). Input is `{"tag": "..."}`. **Find Tag** where Name is the tag (limit 1), read its Name, and only continue if the Name is exactly the requested tag (a second check with Replace Text set to case sensitive, because the Find filter may ignore case). Then MusicBox **Delete Tags** on it (Show When Run off) and **Stop and Output** `deleted`; otherwise output `no such tag`. Sources are in the gateway clone's `local/shortcuts/`.

Until the first two exist, `add_albums` reports that the shortcut is missing and saves nothing, and dry runs say tags couldn't be checked.

```bash
uv tool install .
musicbox-mcp   # speaks MCP over stdio; point your MCP client at this command
```

The shared search and matching code lives in `musicbox_core.py`.

## Alfred integration

Create an Alfred workflow with a keyword trigger (`musicbox`) that runs:

```bash
/Users/YOUR_USERNAME/musicbox-add >/tmp/musicbox-add.log 2>&1 &
```

Then just type `musicbox` in Alfred with a music article open in Safari.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | read from `~/.config/musicbox/api_key` | Your Anthropic API key |
| `MUSICBOX_MODEL` | `claude-haiku-4-5-20251001` | Claude model to use |

## How matching works

The script uses a multi-strategy approach to find albums on Apple Music:

1. **Direct search** — searches iTunes with `entity=album` for the full "Artist Title" query
2. **Cleaned search** — strips special characters and retries
3. **Title-only search** — searches just the album name
4. **Artist catalog lookup** — finds the artist on iTunes, then scans their full album catalog (catches albums missing from search index, like Charli xcx's BRAT)

All results are verified with fuzzy matching (artist + title word overlap with stop word filtering) to reject false positives.
