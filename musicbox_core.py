"""Shared iTunes search, fuzzy matching and MusicBox URL-scheme logic.

Used by the musicbox-add CLI and the musicbox-mcp server. No API-key handling here.
"""

import json, subprocess, urllib.request, urllib.parse, urllib.error, re, unicodedata

def normalize(s):
    """Normalize a string for fuzzy comparison: lowercase, strip accents, remove punctuation."""
    s = s.lower().strip()
    # Strip accents
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    # Remove punctuation and extra whitespace
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

STOP_WORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
              "de", "do", "da", "dos", "das", "del", "la", "le", "les", "el",
              "dj", "mc", "feat", "featuring", "vs", "ep", "single", "vol", "ii"}

def words_overlap(a, b):
    """Return the fraction of meaningful words in `a` that appear in `b`."""
    words_a = set(normalize(a).split()) - STOP_WORDS
    words_b = set(normalize(b).split()) - STOP_WORDS
    if not words_a:
        return 0.0
    return len(words_a & words_b) / len(words_a)

def is_good_match(search_artist, search_title, result_artist, result_album):
    """Check if an iTunes result is a reasonable match for what we searched."""
    # Artist overlap in both directions (handles "CCL & Ciel" vs "Ciel & CCL")
    artist_fwd = words_overlap(search_artist, result_artist)
    artist_rev = words_overlap(result_artist, search_artist)
    artist_overlap = max(artist_fwd, artist_rev)

    # Title overlap: check search title words in result album
    title_overlap = words_overlap(search_title, result_album)

    # For "Various Artists", only require title match
    if normalize(search_artist) in ("various artists", "various", "va"):
        return title_overlap >= 0.5

    # For everything else, require BOTH artist AND title overlap
    if artist_overlap >= 0.3 and title_overlap >= 0.3:
        return True

    return False

def search_itunes_album(query, search_artist="", search_title="", verify=True):
    """Search iTunes for an album and return the Apple Music album URL."""
    encoded = urllib.parse.quote(query)
    url = f"https://itunes.apple.com/search?media=music&entity=album&limit=5&term={encoded}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        for result in data.get("results", []):
            album_url = result.get("collectionViewUrl")
            album_name = result.get("collectionName", "")
            artist_name = result.get("artistName", "")
            if not verify or is_good_match(search_artist, search_title, artist_name, album_name):
                return album_url, f"{artist_name} — {album_name}"
    except Exception:
        pass
    return None, None

def search_artist_albums(artist, title):
    """Fallback: find artist ID, then search their album catalog."""
    try:
        # Step 1: find the artist
        encoded = urllib.parse.quote(artist)
        url = f"https://itunes.apple.com/search?media=music&entity=musicArtist&limit=3&term={encoded}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)

        for artist_result in data.get("results", []):
            artist_id = artist_result.get("artistId")
            if not artist_id:
                continue

            # Step 2: get their albums
            lookup_url = f"https://itunes.apple.com/lookup?id={artist_id}&entity=album&limit=100"
            with urllib.request.urlopen(lookup_url, timeout=10) as resp:
                albums_data = json.load(resp)

            # Step 3: find matching album by title
            norm_title = normalize(title)
            for r in albums_data.get("results", []):
                if r.get("wrapperType") != "collection":
                    continue
                album_name = r.get("collectionName", "")
                # Check if the title words appear in the album name
                if words_overlap(title, album_name) >= 0.5:
                    album_url = r.get("collectionViewUrl")
                    return album_url, f"{r.get('artistName', '?')} — {album_name}"
    except Exception:
        pass
    return None, None

def search_with_fallbacks(artist, title):
    """Try multiple search strategies to find the right album."""
    # Strategy 1: full "Artist - Title" query
    url, info = search_itunes_album(f"{artist} {title}", artist, title)
    if url:
        return url, info

    # Strategy 2: clean special chars and retry
    cleaned_artist = re.sub(r'[*"\'()&]', ' ', artist).strip()
    cleaned_title = re.sub(r'[*"\'()]', '', title).strip()
    url, info = search_itunes_album(f"{cleaned_artist} {cleaned_title}", artist, title)
    if url:
        return url, info

    # Strategy 3: just the album title (helps when artist name is unusual)
    url, info = search_itunes_album(cleaned_title, artist, title)
    if url:
        return url, info

    # Strategy 4: artist catalog lookup (finds albums missing from search index)
    url, info = search_artist_albums(artist, title)
    if url:
        return url, info

    return None, None

def add_to_musicbox(apple_music_url):
    """Add a URL to MusicBox via its URL scheme (background)."""
    encoded = urllib.parse.quote(apple_music_url, safe="")
    musicbox_url = f"musicbox://add?url={encoded}"
    subprocess.run(["open", "-g", musicbox_url], capture_output=True, timeout=5)

def add_many_to_musicbox(apple_music_urls):
    """Open all URLs in MusicBox via one musicbox://add call (single dialog)."""
    joined = "\n".join(apple_music_urls)
    encoded = urllib.parse.quote(joined, safe="")
    subprocess.run(["open", f"musicbox://add?url={encoded}"], capture_output=True, timeout=5)
