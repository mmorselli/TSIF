# ARK Login

Automatizza i tentativi di accesso a un server pieno di **ARK: Survival
Ascended**, usando esclusivamente la finestra del processo `ArkAscended`.

## Avvio

1. Apri `config.json` e controlla `server_number` ed
   `event_screen_enabled`. Il server cercato deve essere la prima riga della
   lista: se non è lì, il programma lo considera assente e non fa clic.
2. Avvia ARK in finestra e raggiungi una qualunque delle schermate descritte
   nelle specifiche.
3. Esegui `arklogin.bat` oppure fai doppio clic sul file. Questo launcher usa
   sempre il Python contenuto in `venv`.
4. Per fermare il programma, premi `Ctrl+C` nella sua finestra oppure chiudila.

Se l'ambiente non è ancora pronto, esegui una volta `avvia_arklogin.bat`: crea
il `venv`, se necessario, e installa automaticamente le librerie richieste.

## Configurazione

- `server_number`: numero univoco del server; la configurazione corrente usa
  `6468`, come gli screenshot di riferimento.
- `event_screen_enabled`: se `true`, conferma la schermata facoltativa dei
  mod/evento; se `false`, la riconosce ma non fa clic.
- `active_poll_interval_seconds`: intervallo tra controlli mentre ARK è in
  primo piano; `0.2` secondi rende rapidi i tentativi.
- `foreground_reacquire_interval_seconds`: ARK viene riportato in primo piano
  soltanto dopo 5 secondi, lasciando il tempo di selezionare la console e
  premere `Ctrl+C`.
- `same_action_retry_seconds`: intervallo minimo prima di ripetere lo stesso
  pulsante. Un pulsante diverso può essere premuto subito.
- `post_cancel_wait_seconds`: attesa tra `CANCEL` e `BACK`.
- `success_unknown_confirm_seconds`: durata minima usata per confermare che la
  schermata di connessione sia scomparsa.
- `success_pause_seconds`: pausa senza riattivazione forzata di ARK dopo una
  probabile connessione riuscita.
- `unchanged_ocr_refresh_seconds` e `visual_change_threshold`: regolano la
  cache visiva che evita di ripetere OCR su una schermata invariata.
- `full_scan_fallback_seconds`: intervallo minimo tra due letture complete
  quando i profili a zone non riconoscono lo stato.
- `restore_mouse_position`: riporta il puntatore dov'era dopo ogni clic.

Il riconoscimento usa piccole zone proporzionali dedicate allo stato atteso,
al pulsante e agli eventuali popup. Non legge normalmente l'intera lista
server: controlla intestazione, prima riga e pulsante `JOIN`. Le coordinate
restano proporzionali, quindi funzionano con dimensioni diverse della finestra.
Una lettura completa viene mantenuta come recupero dopo resize o schermate
inattese.

L'OCR individua anche il centro effettivo dei pulsanti. Le posizioni in
`click_positions` sono usate soltanto come fallback quando il testo del
pulsante non viene localizzato.

## Sicurezza e diagnosi

Il programma:

- non fa clic se titolo e nome del processo non corrispondono;
- verifica focus e dimensioni della finestra di nuovo subito prima di ogni
  clic;
- se un tentativo avanzato termina con `NETWORK FAILURE / Server full`,
  preme `ACCEPT`, attende la schermata iniziale, preme `PRESS TO START` e
  riparte da `JOIN GAME`;
- verifica tramite OCR il numero del server nella prima riga prima dei
  pulsanti `JOIN`;
- non preme `BACK` durante un normale tentativo: lo fa soltanto dopo aver
  rilevato `CONNECTION FAILED` e premuto una sola volta `CANCEL`;
- dopo `connecting`, se la UI di login scompare stabilmente, sospende i clic e
  non ruba il focus per il tempo configurato;
- salva i dettagli in `arklogin.log`.

Per osservare il funzionamento senza produrre clic:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --dry-run --verbose
```

Per verificare e misurare i profili OCR ottimizzati su tutti gli screenshot:

```powershell
.\venv\Scripts\python.exe .\arklogin.py --check-images --verbose
```
