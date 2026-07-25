# ARK Login

Automates login attempts to a full **ARK: Survival Ascended** server while
interacting exclusively with the `ArkAscended` process window.

## Running

1. Open `config.json` and check `server_number` and
   `event_screen_enabled`. The target server must be in the first list row. If
   it is not there, the application treats it as unavailable and does not
   click.
2. Start ARK in windowed mode and navigate to any screen described in the
   specifications.
3. Run or double-click `arklogin.bat`. This launcher always uses the Python
   interpreter from `venv`.
4. To stop the application, press `Ctrl+C` in its console window or close it.

If the environment is not ready yet, run `setup_arklogin.bat` once. It creates
the `venv`, when necessary, and automatically installs the required libraries.

## Configuration

- `server_number`: unique server number. The current configuration uses
  `6468`, matching the reference screenshots.
- `event_screen_enabled`: when `true`, confirms the optional event/mod screen.
  When `false`, recognizes it without clicking.
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

## Safety and diagnostics

The application:

- does not click unless both the window title and process name match;
- rechecks focus and window bounds immediately before each click;
- when an advanced attempt ends with `NETWORK FAILURE / Server full`, presses
  `ACCEPT`, waits for the start screen, presses `PRESS TO START`, and resumes
  from `JOIN GAME`;
- verifies the server number in the first row through OCR before pressing
  `JOIN`;
- does not press `BACK` during a normal attempt. It does so only after
  detecting `CONNECTION FAILED` and pressing `CANCEL` once;
- after `connecting`, pauses clicks and avoids reclaiming focus for the
  configured duration when the login UI disappears consistently;
- writes diagnostic details to `arklogin.log`.

To observe the application without sending clicks:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --dry-run --verbose
```

To check and benchmark optimized OCR profiles against all screenshots:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --check-images --verbose
```
