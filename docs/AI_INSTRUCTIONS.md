# GOAL

Write a Python application that automatically clicks buttons in a game window
to make the login process easier.

# CONTEXT

ARK: Survival Ascended servers are often full at 70/70 players, requiring a
long series of login attempts.

The game runs in windowed mode. Its window title contains
`ARK: Survival Ascended` followed by a frequently changing version number,
which must be ignored.

The main game process is named `ArkAscended`.

The login process has several steps illustrated by the screenshots in
`D:\dati\prg\arklogin\docs\`:

- `01_first_screen.png`: click `JOIN GAME` on the first card.
- `02_join-server.png`: the server list appears. Press `JOIN` in the
  bottom-right corner because the last attempted server is preselected. The
  server number should be configurable; it is `6448` in this example and is
  unique on the screen. If that number is present, the selected row is correct.
- `03_optional_event.png`: this screen appears only while an event is active,
  so its handling must be configurable. Press `JOIN` to continue.
- `04_connection_failed.png`: when the connection fails, the
  `CONNECTION FAILED` screen appears. Press `CANCEL`.
- `05_cancel_after_connection_failed.png`: after pressing `CANCEL`, login
  remains blocked temporarily, so press `BACK`. This returns to the exact state
  shown in `01_first_screen.png`, allowing the loop to resume.
- When `JOINING FAILED / Unknown Error` appears, press `OK`. The dialog closes
  and returns to the server list. If the list is empty, wait until the target
  server becomes available again.
- `07_dlc_owned.png`: this screen may open if an unintended click reaches
  `DLC PACKS` instead of `JOIN GAME`. Press the centered `BACK` button and
  return to `01_first_screen.png`.

# REQUIREMENTS

- Write the requested application and install all required libraries
  automatically.
- Move the hardcoded server number and event-presence parameters into a
  configuration file.
- Keep every button label and screen marker searched by OCR in `lang.json`, so
  they can be replaced or extended for game languages other than English.
- Allow the rotating file log to be disabled from `config.json` without
  suppressing console messages.

# PERFORMANCE REQUIREMENTS

Performance is part of the application behavior, not an optional refinement.
Future changes should preserve a short delay between a screen transition and
the next valid click while avoiding continuous full-screen OCR.

## OCR workload

- Capture only the ARK client area. Do not process the desktop, title bar, or
  window borders.
- Use proportional, state-specific OCR regions for screen markers, the first
  server row, and action buttons. Do not read the entire canvas during the
  normal recognition path.
- Assume that the target server is either in the first row or absent. Do not
  scan or parse the complete server table.
- Downscale large captures before OCR and upscale very small captures enough
  to keep text readable. Preserve aspect ratio and proportional coordinates.
- Keep RapidOCR orientation classification disabled while the ARK interface
  uses horizontal text only.
- Discard detections below the configured confidence threshold as early as
  possible.
- Normalize configured language aliases once at startup and normalize each OCR
  result once before performing all comparisons.

## Adaptive recognition

- Select OCR profiles from the current recognized state and the last successful
  action. Check the most likely next profile first and stop after a credible
  match.
- Require state-specific evidence before accepting a partial-region result.
  When a state has an actionable button, its OCR anchor must be present.
- Build a small grayscale visual signature from the active regions before OCR.
  Reuse the cached recognition while the signature is unchanged and the
  configured refresh interval has not expired.
- Cache masks by profile and window size, but keep the cache bounded so
  repeated resizing cannot cause unbounded memory growth.
- Invalidate visual and coordinate caches immediately after a window resize or
  profile change.
- Keep full-screen OCR as a throttled recovery path for startup, resizing,
  unknown states, or failed regional profiles. It must not become the regular
  polling path.
- After probable login success, switch to passive polling and do not repeatedly
  reclaim ARK focus.

## Input latency

- Schedule active checks from the beginning of the previous scan so OCR time
  does not add another complete polling interval.
- Use the OCR-detected normalized button center when available. Use configured
  proportional coordinates only as a fallback.
- Keep hover, mouse-down, and restoration delays short and configurable.
- Debounce repeated presses per action. A different action made available by a
  screen transition must be allowed immediately.
- Change the expected OCR profile chain immediately after a successful click.
- Recheck foreground ownership and client bounds immediately before
  mouse-down. Cancel stale input instead of clicking an outdated position.
- Keep foreground reacquisition scheduling separate from the fast active loop
  so the console remains reachable for `Ctrl+C`.

## Adding or changing screens

When a new screen or dialog is introduced:

1. Define the smallest proportional regions that contain its unique marker and
   actionable button.
2. Add it to the state and post-action profile chains in expected transition
   order.
3. Define the evidence required to trust its regional OCR result.
4. Add a reference screenshot or synthetic fixture and tests for recognition,
   button anchoring, and at least one resized window.
5. Run the complete unit-test suite and `arklogin.py --check-images`. Compare
   OCR timings and verify that existing screens still use regional profiles
   instead of unnecessary full-screen fallbacks.
