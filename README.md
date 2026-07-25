# ARK Login V1.0

Automates login attempts to a full **ARK: Survival Ascended** server while
interacting exclusively with the `ArkAscended` process window.

## Running

1. Run or double-click `setup_arklogin.bat` once. It creates the local
   configuration files, prepares the `venv`, installs the required libraries,
   and starts the application.
2. Open `config.json` and check `server_number` and
   `event_screen_enabled`. The target server must be in the first list row. If
   it is not there, the application treats it as unavailable and does not
   click.
3. Start ARK in windowed mode and navigate to any screen described in the
   specifications.
4. Run or double-click `arklogin.bat`. This launcher always uses the Python
   interpreter from `venv`.
5. To stop the application, press `Ctrl+C` in its console window or close it.

During setup, `config.json` is copied from `config.json.example` and
`lang.json` from `lang.json.example` only when the destination file does not
already exist. Running setup again updates dependencies but never overwrites
either local file.

## Configuration

`config.json` and `lang.json` contain user-specific settings and are excluded
from Git. Their version-controlled `.example` files provide defaults for new
installations. After updating the application, compare existing local files
with the corresponding examples and manually add any newly introduced entries.

- `server_number`: unique server number. The current configuration uses
  `6468`, matching the reference screenshots.
- `event_screen_enabled`: when `true`, confirms the optional event/mod screen.
  When `false`, recognizes it without clicking.
- `language_file`: path to the JSON file containing every button label and
  screen marker used by OCR. Relative paths are resolved from the application
  directory.
- `log_file_enabled`: when `true`, writes diagnostic messages to the rotating
  `arklogin.log` file. Set it to `false` to disable file logging while keeping
  console messages available.
- `active_poll_interval_seconds`: interval between checks while ARK is in the
  foreground. The `0.2`-second default keeps attempts responsive.
- `foreground_reacquire_interval_seconds`: waits 5 seconds before bringing ARK
  back to the foreground, leaving enough time to select the console and press
  `Ctrl+C`.
- `same_action_retry_seconds`: minimum interval before retrying the same
  button. A different button can be pressed immediately.
- `post_cancel_wait_seconds`: delay between `CANCEL` and `BACK`.
- `success_unknown_confirm_seconds`: minimum time used to confirm that the
  connection screen has disappeared.
- `success_pause_seconds`: pause without forced ARK focus after a probable
  successful login.
- `unchanged_ocr_refresh_seconds` and `visual_change_threshold`: control the
  visual cache that avoids repeated OCR on an unchanged screen.
- `full_scan_fallback_seconds`: minimum interval between full-screen scans
  when region profiles cannot recognize the current state.
- `restore_mouse_position`: restores the previous pointer position after each
  click.

Recognition uses small proportional regions dedicated to the expected state,
button, and possible popups. It does not normally read the complete server
list: it checks the header, first row, and `JOIN` button. Coordinates remain
proportional, so they work across different window sizes. A full scan remains
available as a recovery path after resizing or unexpected screens.

OCR also locates the actual center of each button. Values in `click_positions`
are used only as fallbacks when the button text cannot be located.

## Language customization

All text searched on the game screen is defined in `lang.json`. Each entry is
a list of accepted OCR aliases. To support another game language, edit the
local file and translate every value while preserving the keys. Alternatively,
copy `lang.json.example` to another filename, translate it, and set
`language_file` in `config.json` to that filename.

Multiple aliases can be retained when a label has alternative translations or
OCR frequently returns a predictable variant:

```json
{
  "join_game": ["PLAY ONLINE", "ENTER GAME"]
}
```

The actual language file must contain all entries found in
`lang.json.example`; startup stops with a clear error if an entry is missing
or empty. Files are read as UTF-8, and matching supports Unicode letters and
digits.

## Performance optimizations

The recognition and input loop is designed to minimize the time between a
screen transition and the next valid click without continuously running
expensive full-screen OCR.

### Reduced OCR workload

- Only the ARK client area is captured; the desktop, title bar, and window
  borders are excluded.
- State-specific OCR profiles expose only the small proportional regions where
  useful text can appear, such as a popup title, the first server row, or an
  action button. The rest of the image is masked before OCR.
- The server browser does not parse the complete table. It checks only the
  header, the first row, and the `JOIN` area because the target server is
  assumed to be either first or absent.
- Profiles are ordered according to the current state and the last action.
  The most likely next screen is checked first, and recognition stops as soon
  as the result contains the required evidence and button anchor.
- Images wider than 1280 pixels are reduced before OCR, limiting the amount of
  data processed on large windows. Very small windows are enlarged to at least
  960 pixels wide to preserve text readability.
- ARK renders horizontal UI text, so RapidOCR orientation classification is
  disabled and each detection is processed only once.
- OCR results below `ocr_min_confidence` are discarded immediately.
- Language aliases are normalized once when `lang.json` is loaded. Each
  captured OCR result is also normalized once before all screen comparisons.

### Adaptive scanning and caching

- Before invoking OCR, the application builds a grayscale `64x36` visual
  signature from the currently relevant regions. If the signature has not
  changed enough, the previous recognition result is reused.
- `unchanged_ocr_refresh_seconds` periodically forces a fresh OCR pass even on
  an apparently static screen, while `visual_change_threshold` controls how
  much visual difference invalidates the cache.
- Region masks are cached by profile and window size. The cache is bounded to
  12 entries so repeated scans avoid rebuilding masks without accumulating
  memory after many resizes.
- Full-screen OCR is a recovery path, not the normal path. It runs at startup,
  after a resize, when no state-specific profile is available, or after a
  profile miss. Repeated fallback scans are limited by
  `full_scan_fallback_seconds`.
- A resize invalidates the visual cache immediately, preventing an obsolete
  recognition from being reused with new coordinates.
- After probable login success, scanning switches from
  `active_poll_interval_seconds` to the slower
  `success_passive_poll_seconds`, reducing CPU use and avoiding unnecessary
  focus changes.

### Fast and safe input

- The active loop starts a new check every `active_poll_interval_seconds`
  (`0.2` seconds by default), measured from the beginning of the previous
  scan, so OCR time does not add another full polling delay.
- OCR supplies the normalized center of the detected button directly to the
  click routine. Proportional configured coordinates are used only when the
  button label is unavailable.
- Clicking uses short configurable hover and hold delays. The current defaults
  are `0.05` seconds for each, with a `0.02`-second pointer restoration delay.
- Debouncing applies only to repeated presses of the same action through
  `same_action_retry_seconds`. A newly available different button can be
  pressed immediately.
- Immediately after a click, the expected OCR profiles are changed to match
  the likely destination screen. This avoids waiting for a generic scan to
  discover the transition.
- Focus and client bounds are rechecked immediately before mouse-down. A stale
  click is cancelled instead of wasting time in an incorrect state or at an
  outdated position.

## Safety and diagnostics

The application:

- does not click unless both the window title and process name match;
- rechecks focus and window bounds immediately before each click;
- when an advanced attempt ends with `NETWORK FAILURE / Server full`, presses
  `ACCEPT`, waits for the start screen, presses `PRESS TO START`, and resumes
  from `JOIN GAME`;
- when `JOINING FAILED / Unknown Error` appears, presses `OK`, returns to the
  server list, and waits without clicking until the configured server is back
  in the first row;
- if an unintended click opens the `DLC OWNED` screen, recognizes it and
  presses its centered `BACK` button to return to `JOIN GAME`;
- verifies the server number in the first row through OCR before pressing
  `JOIN`;
- does not press `BACK` during a normal attempt. It does so only after
  detecting `CONNECTION FAILED` and pressing `CANCEL` once;
- after `connecting`, pauses clicks and avoids reclaiming focus for the
  configured duration when the login UI disappears consistently;
- writes diagnostic details to `arklogin.log` when `log_file_enabled` is
  enabled.

To observe the application without sending clicks:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --dry-run --verbose
```

To check and benchmark optimized OCR profiles against all screenshots:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --check-images --verbose
```
