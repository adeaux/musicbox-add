# Project History

## The Goal

Take a music article (like [RA's Best Records of 2024](https://ra.co/features/4404)) and add every album to [MusicBox](https://apps.apple.com/app/musicbox-save-music-for-later/id1614730313) automatically.

## Phase 1: Apple Shortcuts (abandoned)

Started by building an iOS/macOS Shortcut via the Shortcuts Playground plugin. This went through ~10 iterations over several hours and hit wall after wall:

### Attempt 1: HTTP fetch + Apple Intelligence
- **Problem:** RA.co is JavaScript-rendered. `Get Contents of URL` returns an empty HTML shell — the article content isn't in the raw HTML.
- **Problem:** Apple Intelligence's on-device `askllm` action choked on large text input (hung indefinitely).

### Attempt 2: Safari JavaScript extraction + Apple Intelligence
- Switched to `Run JavaScript on Web Page` from Safari Share Sheet — this works because Safari has already rendered the JS.
- **Problem:** `document.body.innerText` returns the *entire* page (nav, footer, ads) — too much for Apple Intelligence's small context window. Hung for minutes.

### Attempt 3: Smart JS extraction + Gemini API
- Smarter JavaScript: tries `<article>`, `<main>`, `[role="main"]` first, strips nav/footer, caps at 12K chars.
- Replaced Apple Intelligence with Google Gemini API via HTTP POST.
- **Problem:** `WFHTTPBodyType: File` doesn't reliably preserve custom `Content-Type` headers. Gemini rejected the request.
- **Problem:** `WFHTTPBodyType: JSON` ignores `WFRequestVariable` entirely — it expects inline `WFJSONValues`. Posted an empty body.
- **Problem:** Gemini API key had depleted credits (`limit: 0`). Then Gemini 2.0 Flash was removed from the free tier entirely.

### Attempt 4: Claude API
- Switched to Claude (Anthropic) API — the user already had access.
- Fixed the HTTP body issue by going back to `File` type with Content-Type header (the JSON escaping was now fixed with backslash handling).
- Fixed Gemini's deep key path `candidates.1.content.parts.1.text` → Claude's simpler `content[0].text`, but still needed step-by-step dictionary chaining because Shortcuts doesn't support array indexing in dot-notation key paths.
- **Problem:** The Claude API call worked, iTunes search worked, but the MusicBox `AddMusicAppIntent` kept failing with "Please choose a value for each parameter."

### MusicBox AppIntent issues
- **TeamIdentifier:** The placeholder `0000000000` needed to be the real `SJF85Q6EJ9` (found via `codesign -dv`).
- **Even with the correct TeamIdentifier:** The action still failed. The plist-based AppIntent import never resolved parameters properly.
- **Manual fix worked:** Deleting the broken action in Shortcuts.app and re-adding it from the action library worked — but the URL it received was nil because `results.1.collectionViewUrl` doesn't work as a key path.
- **Still failed:** Even after fixing the key path to step-by-step extraction, the MusicBox action kept showing the parameter error.

### Attempt 5: URL scheme instead of AppIntent
- Discovered MusicBox's URL schemes via `Info.plist`: `musicbox://` and `musicboxopenitem://`.
- `musicbox://add?url=<encoded Apple Music URL>` opens an "Add Music" dialog with the URL pre-filled.
- This worked! But spawned one dialog per album (25 dialogs for 25 albums).

### Share Sheet integration attempts
- **Automator Quick Action:** Created a `.workflow` in `~/Library/Services/` — never showed up in the Share Sheet.
- **Shortcuts wrapper:** Built a minimal Shortcut with "Run Shell Script" — the Shortcuts sandbox couldn't access `~/musicbox-add`.

**Verdict:** Abandoned Shortcuts entirely. Too many layers of abstraction, each with its own quirks and failure modes.

## Phase 2: Python CLI (final solution)

A ~150-line Python script that does everything the Shortcut tried to do, but reliably:

### How it works
1. `osascript` runs JavaScript in Safari to grab `document.body.innerText` (same trick as the Shortcut's `Run JavaScript on Web Page`, but from the command line)
2. Claude Haiku extracts artist-album pairs via the Anthropic API
3. iTunes Search API finds Apple Music URLs with multi-strategy fallback
4. Opens all albums in MusicBox via `musicbox://add?url=<newline-delimited URLs>` — **one dialog for all albums**

### Key discoveries
- **MusicBox batch add:** Newline-delimited URLs in a single `musicbox://add?url=` call opens one dialog with all albums listed.
- **iTunes Search API gaps:** Some albums (like Charli xcx's BRAT) don't appear in text search results but exist on Apple Music. The fallback strategy of looking up the artist's catalog via `/lookup?id=<artistId>&entity=album` catches these.
- **Fuzzy match verification:** Without verification, iTunes returns garbage matches for queries it can't find (e.g., searching "Letters to a Future Palestine" returns "Light Music for Focused Daytime Work"). Word-overlap scoring with stop-word filtering catches these false positives.
- **Cost:** ~$0.007 per run using Claude Haiku. About 140 runs per dollar.

### Integration
- **Alfred workflow:** Type `musicbox` to trigger. Runs in background, shows macOS notification when done.
- **Terminal:** `musicbox-add` or `musicbox-add --dry-run`
- **Repo:** [github.com/adeaux/musicbox-add](https://github.com/adeaux/musicbox-add)

## Lessons Learned

### When to use Shortcuts vs. scripts
- **Shortcuts are good for:** Simple, linear workflows with 1-5 first-party actions. Share Sheet triggers. Quick UI glue.
- **Shortcuts are bad for:** API calls with custom JSON bodies, complex error handling, loops with conditional logic, third-party AppIntents that need specific parameter wiring, anything that requires debugging.
- **The tipping point:** If you need to POST JSON to an API, you've already outgrown Shortcuts.

### Shortcuts-specific gotchas
- `WFHTTPBodyType: JSON` uses `WFJSONValues` (inline), NOT `WFRequestVariable`
- `WFHTTPBodyType: File` might override Content-Type headers
- Deep key paths with array indices (`results.1.field`) don't work — chain separate Get Dictionary Value + Get Item from List actions
- Third-party AppIntents rarely import correctly from plist XML — TeamIdentifier, parameter names, and action resolution all need to match exactly
- The Shortcuts sandbox blocks access to home directory scripts
- Apple Intelligence (`askllm`) has severe context limits and no error handling

### MusicBox-specific discoveries
- Bundle ID: `br.com.marcosatanaka.musicbox`
- Team ID: `SJF85Q6EJ9`
- URL schemes: `musicbox://add?url=<url>`, `musicboxopenitem://<url>`
- Batch add: newline-delimited URLs in a single `musicbox://add?url=` call
- AppIntents: `AddMusicAppIntent` (url, tags, notes), `AddMusicManuallyAppIntent`, `GetMusic`, `CreateTag`, `MarkAsPlayed`, `DeleteMusic`, `AddTagAppIntent`
- `openAppWhenRun: false` on most intents — designed for background execution
- App data stored in CloudKit (no local SQLite to write to)
