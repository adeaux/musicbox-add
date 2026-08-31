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
