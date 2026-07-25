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

# REQUIREMENTS

- Write the requested application and install all required libraries
  automatically.
- Move the hardcoded server number and event-presence parameters into a
  configuration file.
- Keep every button label and screen marker searched by OCR in `lang.json`, so
  they can be replaced or extended for game languages other than English.
- Allow the rotating file log to be disabled from `config.json` without
  suppressing console messages.
