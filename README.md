# Min Oda

Henter handlehistorikken din fra oda.com og bruker den til to ting:
bygge handlelister (eller supplere kurven med varer som mangler), og
vise mønstre i hva du faktisk handler. Alt kjører lokalt. Ingenting
forlater maskinen.

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

## Start

```sh
uv run min-oda
```

Starter appen på `http://localhost:8000`. Ved oppstart sjekkes alderen
på `data/orders.json`. Hvis den er eldre enn 24 timer, hentes nye ordrer
fra Oda og CSV-ene bygges på nytt. Du kan også oppdatere når som helst
via knappen `⟳` øverst til høyre.

Første gang du starter, eller hvis cookien er utløpt, vises en
advarsel i navigasjonen og appen kjører videre med eksisterende data.

### Hente data manuelt

Hvis du vil tvinge en oppdatering fra terminalen:

```sh
uv run python fetch_orders.py
```

Hvis ingen av de antatte endepunktene treffer, finn riktig URL i DevTools:

1. Åpne `oda.com` → **Min konto** → **Mine ordrer** med F12 oppe.
2. Network-fanen, filtrer XHR/Fetch.
3. Finn en request som returnerer JSON med ordrelisten.
4. Høyreklikk → Copy → Copy URL.
5. Kjør `uv run python fetch_orders.py --url '<URL>'`.

## Funksjonalitet

To faner:

**Handleliste** (`/handleliste`) har to moduser:

- *Legg til handlekurv* (default): sammenligner faste varer med kurven
  din på oda.com akkurat nå og viser kun det som mangler. Juster antall,
  opprett en restliste på Oda, legg den til kurven derfra.
- *Lagre som handleliste*: bygg en komplett ukehandel-liste basert på
  kjøpskadens per varetype, og lagre den som en gjenbrukbar mal på Oda.

Begge moduser viser status-pills (forfalt / snart / i rute) og hvor
mange dager til (eller siden) varen typisk trenger å handles. Slidere
for syklus, maks antall varer og maks per kategori. Rader som dukker
opp fordi du har utvidet et filter, markeres med en aksent-stripe så
du ser hva som er "ekstra" sammenlignet med default.

**Innsikt** (`/innsikt`): hva sier handleturene om husstanden?

- Nøkkeltall (totalt brukt, snitt per ordre, frekvens)
- Månedlig forbruk-graf (med årlig sesongmønster)
- Husstandens DNA: varer i mer enn 40 % av ordrene
- Matkultur (norsk / italiensk / asiatisk / tex-mex / indisk)
- Kokestil (råvarer vs. ferdigmat) + prisbevissthet + helsesignaler
- Drikkeprofil (brus / øl / juice / kaffe / vann)
- Topp produkter og kategorier siste 12 mnd
- Sesongprodukter (kun kjøpt visse måneder)
- Året i Oda-måneder (sommerferie-gapet)
- Basket-analyse: hvilke varer havner ofte sammen, og en
  *"når jeg kjøper X, hva følger med?"*-søk

## Sikkerhet

- `.env` og `data/` er gitignored, så cookies og handlehistorikk
  forlater aldri maskinen.
- Det er kun session-cookien som brukes til autentisering, ikke
  brukernavn og passord.
- Cookien utløper etter en stund. Hvis du får 401/403, logg inn på
  nytt og oppdater `.env`.
