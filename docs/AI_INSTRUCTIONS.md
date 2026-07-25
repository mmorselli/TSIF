# OBBIETTIVO

Scrive un programma in python che clicchi in automatico dei pulsanti nella finestra di un gioco per facilitare la procedura di login

# CONTESTO

Il gioco ARK SURVIVAL ASCENDED ha spesso i server pieni 70/70 richiedendo una lunga serie di tentativi per effettuare il login

Il gioco girerà in finestra e la finestra ha il titolo "ARK: Survival Ascended" - contiene anche un numero di versione, che però cambia spesso e quindi non va considerato.

Il processo principale del gioco si chiama ArkAscended

La procedura di Login passa per diversi passaggi, illustrata negli screenshot in D:\dati\prg\arklogin\docs\

- 01_first_screen.png - Nella prima schermata si dovrà cliccare su "JOIN GAME" della prima card
- 02_join-server.png - Apparirà la lista server, qui si dovrà solo premere JOIN in basso a destra perché l'ultimo server provato sarà preselezionato, ma sarebbe opportuno mettere in un parametro il numero di server, in questo caso è 6448 e questo numero è univoco nella schermata, se è presente quel numero la riga del server è quella giusta
- 03_optional_event.png - questa schermata appare solo se c'è un evento in corso e quindi deve poter essere disattivata con un parametro, qui sarà sufficiente premere JOIN per procedere 
- 04_connection_failed.png - se la connessione fallisce appare la schermata CONNECTION FAILED, qui bisogna premere CANCEL
- 05_cancel_after_connection_failed.png - dopo aver premuto CANCEL non sarà possibile connettersi subito perché la schermata di login sarà bloccata, quindi bisognerà premere BACK. Premendo BACK mi riporterà esattamente alla situazione di 01_first_screen.png e il loop  potrà riprendere


# RICHIESTE

- scrivere il programma richiesto installando autonomamente tutte le librerie necessarie
- mettere i parametri per il numero del server e la presenza dell'evento hardcoded in un file di configurazione