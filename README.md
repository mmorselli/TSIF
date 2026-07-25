# ARK Login

Automatizza i tentativi di accesso a un server pieno di **ARK: Survival
Ascended**, usando esclusivamente la finestra del processo `ArkAscended`.

## Avvio

1. Apri `config.json` e controlla `server_number` ed
   `event_screen_enabled`.
2. Avvia ARK in finestra e raggiungi una qualunque delle schermate descritte
   nelle specifiche.
3. Esegui `arklogin.bat` oppure fai doppio clic sul file. Questo launcher usa
   sempre il Python contenuto in `venv`.
4. Per fermare il programma, premi `Ctrl+C` nella sua finestra oppure chiudila.

Se l'ambiente non è ancora pronto, esegui una volta `avvia_arklogin.bat`: crea
il `venv`, se necessario, e installa automaticamente le librerie richieste.

## Configurazione

- `server_number`: il numero univoco del server. Il valore predefinito è `6448`,
  come richiesto nelle specifiche. Gli screenshot forniti mostrano invece
  `6468`: se quello è il server corretto, aggiorna il valore prima dell'avvio.
- `event_screen_enabled`: se `true`, conferma la schermata facoltativa dei
  mod/evento; se `false`, la riconosce ma non fa clic.
- `poll_interval_seconds`: frequenza di controllo. Il valore predefinito è
  `5.0` secondi, così rimane il tempo di selezionare la console e premere
  `Ctrl+C` anche quando ARK viene riportato in primo piano.
- `action_cooldown_seconds`: intervallo minimo tra due clic.
- `post_cancel_wait_seconds`: attesa tra `CANCEL` e `BACK`.
- `restore_mouse_position`: riporta il puntatore dov'era dopo ogni clic.

Il programma usa l'OCR anche per individuare il centro effettivo dei pulsanti,
quindi il clic segue il layout quando la finestra cambia dimensione. Le
posizioni in `click_positions` sono usate soltanto come fallback quando il
testo di un pulsante non viene localizzato.

## Sicurezza e diagnosi

Il programma:

- non fa clic se titolo e nome del processo non corrispondono;
- porta ARK in primo piano e verifica che lo sia prima di ogni clic;
- se un tentativo avanzato termina con `NETWORK FAILURE / Server full`,
  preme `ACCEPT`, attende la schermata iniziale, preme `PRESS TO START` e
  riparte da `JOIN GAME`;
- verifica tramite OCR il numero del server prima dei pulsanti `JOIN`;
- non preme `BACK` durante un normale tentativo: lo fa soltanto dopo aver
  rilevato `CONNECTION FAILED` e premuto `CANCEL`;
- salva i dettagli in `arklogin.log`.

Per osservare il funzionamento senza produrre clic:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --dry-run --verbose
```

Per verificare il riconoscimento su tutti gli screenshot:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --check-images --verbose
```
