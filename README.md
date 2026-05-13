# Oda-analyse

Personlig assistent for oda.com-handelen din. Bygger på din egen
handlehistorikk — alt kjører lokalt, ingenting forlater maskinen.

- **Handleliste**: bygg en gjenbrukbar ukehandel-mal eller suppler
  kurven din med varer du pleier å ha med, basert på faktisk
  kjøpsmønster og kadens per varetype. Skriver listen rett til Oda.
- **Innsikt**: hva sier handleturene om husstanden? Matkultur,
  helsesignaler, drikkeprofil, sesongprodukter, basket-analyse og mer.

> Personlig vibe-koding. Bruker et udokumentert Oda-endepunkt med din egen
> session-cookie — du sender ingenting til en tredjepart, men det kan slutte
> å virke når Oda oppdaterer API-et sitt. Fork eller bruk som template.

## Oppsett

1. Logg inn på oda.com i Firefox.
2. Hent session-cookien:
   - Trykk `F12` → fanen **Storage** (eller **Lagring**) → **Cookies** → `https://oda.com`
   - Kopier verdien til `sessionid` og `csrftoken`.
3. Kopier `.env.example` til `.env` og lim inn verdiene.
4. Installer avhengigheter:
   ```sh
   uv sync
   ```

## Hent data

```sh
make refresh
```

Henter rå ordrehistorikk fra oda.com, lagrer JSON i `data/` og bygger
`data/orders.csv` + `data/lines.csv` som resten av appen leser fra.

Hvis ingen av de antatte endepunktene treffer, finn riktig URL i DevTools:

1. Åpne `oda.com` → **Min konto** → **Mine ordrer** med F12 oppe.
2. Network-fanen, filtrer XHR/Fetch.
3. Finn en request som returnerer JSON med ordrelisten.
4. Høyreklikk → Copy → Copy URL.
5. Kjør `uv run fetch_orders.py --url '<URL>'`.

## Web-app

```sh
make web
```

Starter FastAPI + HTMX-appen på `http://localhost:8000`. To faner:

**Handleliste** (`/handleliste`) — to moduser:

- *Legg til handlekurv* (default): sammenligner faste varer med kurven
  din på oda.com akkurat nå og viser kun det som mangler. Juster antall,
  opprett en restliste på Oda, legg den til kurven derfra.
- *Lagre som handleliste*: bygg en komplett ukehandel-liste basert på
  kjøpskadens per varetype, og lagre den som en gjenbrukbar mal på Oda.

Begge moduser viser status-pills (forfalt / snart / i rute) og hvor
mange dager til (eller siden) varen typisk trenger å handles. Slidere
for syklus, maks antall varer og maks per kategori — rader som dukker
opp fordi du har utvidet et filter markeres med en aksent-stripe så du
ser hva som er "ekstra" sammenlignet med default.

**Innsikt** (`/innsikt`) — hva sier handleturene om husstanden?

- Nøkkeltall (totalt brukt, snitt per ordre, frekvens)
- Månedlig forbruk-graf (med årlig sesongmønster)
- Husstandens DNA — varer i mer enn 40 % av ordrene
- Matkultur (norsk / italiensk / asiatisk / tex-mex / indisk)
- Kokestil (råvarer vs. ferdigmat) + prisbevissthet + helsesignaler
- Drikkeprofil (brus / øl / juice / kaffe / vann)
- Topp produkter og kategorier siste 12 mnd
- Sesongprodukter (kun kjøpt visse måneder)
- Året i Oda-måneder (sommerferie-gapet)
- Basket-analyse: hvilke varer havner ofte sammen, og en
  *"når jeg kjøper X, hva følger med?"*-søk

## Makefile

```sh
make refresh   # hent nye ordrer + bygg CSV
make web       # start web-appen
make all       # refresh + web
```

## Sikkerhet

- `.env` og `data/` er gitignored — cookies og personlig handlehistorikk
  forlater aldri maskinen.
- Session-cookien er den eneste credentialen som brukes — passordet ditt
  rører vi ikke.
- Cookien utløper etter en stund; logg inn på nytt og oppdater `.env` om
  du får 401/403.
