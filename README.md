# Trasferimento ricorsivo proprietà Google Drive

Trasferisce la proprietà di una cartella Drive e di tutto il suo contenuto a un altro utente della stessa organizzazione.

## Setup (una tantum)

1. **Abilita la Google Drive API**
   Vai su [Google Cloud Console](https://console.cloud.google.com/), seleziona (o crea) un progetto, poi
   *API e servizi → Libreria → Google Drive API → Abilita*.

2. **Crea le credenziali OAuth**
   *API e servizi → Credenziali → Crea credenziali → ID client OAuth → tipo "App desktop"*.
   Scarica il JSON e salvalo come `credentials.json` nella stessa cartella dello script.

   > Se richiesto, configura prima la schermata di consenso OAuth (tipo *Interno*).

3. **Installa le dipendenze**
   ```bash
   pip install google-api-python-client google-auth-oauthlib
   ```

## Uso

```bash
# prova, non modifica nulla
python transfer-drive-ownership.py <ID_CARTELLA> nuovo.owner@azienda.it --dry-run

# trasferimento effettivo
python transfer-drive-ownership.py <ID_CARTELLA> nuovo.owner@azienda.it
```

L'ID cartella è l'ultima parte dell'URL: `https://drive.google.com/drive/folders/<ID_CARTELLA>`

Al primo avvio si apre il browser per l'autorizzazione; il token viene salvato in `token.json`.
