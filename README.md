# Radar Concorsi

Sito per seguire i concorsi di radiodiagnostica: login con Google, elenco dei concorsi seguiti, nuovi bandi da inPA ed email giornaliera con le novità (graduatorie, date delle prove, avvisi), riassunte con GitHub Models.

- **Sito**: GitHub Pages (`index.html`, `app.js`, `style.css`, `firebase-config.js`)
- **Dati e login**: Firebase Authentication (Google) + Firestore (`firestore.rules`)
- **Controllo giornaliero**: GitHub Actions (`.github/workflows/controllo-giornaliero.yml` → `check_concorsi.py`)

## Segreti da impostare (Settings → Secrets and variables → Actions)
| Nome | Contenuto |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | JSON della chiave dell'account di servizio Firebase |
| `GMAIL_USER` | indirizzo Gmail da cui inviare le email |
| `GMAIL_APP_PASSWORD` | password per le app di quell'account Gmail |
