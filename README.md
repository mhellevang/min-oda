# Min Oda

En webapp som henter handlehistorikken din fra oda.com og bruker den til
to ting: foreslå hva du bør handle nå (basert på hvor ofte du faktisk
kjøper hver varetype), og vise mønstre i hva husstanden handler. Kjører
lokalt eller på egen server. Ingenting deles med andre enn oda.com.

## Kom i gang

```sh
uv sync
uv run min-oda
```

Appen starter på `http://localhost:8000`. Eneste forutsetning er at du
er logget inn på oda.com i en nettleser på samme maskin, appen leser
session-cookien derfra (se [Innlogging mot Oda](#innlogging-mot-oda)).

Ved oppstart hentes ordrehistorikken din hvis den lokale kopien er
eldre enn 24 timer. Du kan også oppdatere når som helst med `⟳`-knappen
øverst til høyre. Er cookien utløpt vises en advarsel i navigasjonen,
og appen kjører videre med eksisterende data til du har logget inn på
oda.com igjen.

Alternativt via Docker: `docker compose up --build`.

## Handleliste

`/handleliste` regner ut kjøpskadens per *varetype* (brød, melk, bleier
...), ikke per produkt, så bytte mellom merker teller som samme behov.
Hver rad viser status (forfalt / snart / i rute), dager til eller siden
varen typisk trengs, og foreslått antall. To moduser:

- **Legg til handlekurv** (default): sammenligner faste varer med kurven
  din på oda.com akkurat nå og viser kun det som mangler. Ett klikk
  legger alt i kurven.
- **Lagre som handleliste**: bygger en komplett ukehandel-liste uavhengig
  av kurven og lagrer den som gjenbrukbar liste på Oda.

Slidere bak tannhjulet styrer syklus, maks antall varer og maks per
kategori. Rader som bare dukker opp fordi et filter er utvidet, markeres
med en aksent-stripe.

Lista kan justeres direkte:

- **×** på en rad skjuler produktet fra forslag (typisk bleier i en
  størrelse barnet vokste forbi). En annen variant av samme varetype tar
  automatisk over. Skjulte varer listes nederst og kan hentes tilbake.
- **⇄** på en rad søker i Oda-katalogen og lar deg velge et annet produkt
  som fast representant for varetypen, også varer du aldri har kjøpt.
- **Søk hos Oda** ved søkefeltet finner katalogvarer utenfor de faste
  forslagene og legger dem på lista som engangsvarer.

Ingenting sendes til Oda før du trykker en av bulk-knappene.

## Innsikt

`/innsikt` svarer på hva handleturene sier om husstanden:

- Nøkkeltall: totalt brukt, snitt per ordre, handlefrekvens
- Månedlig forbruk med årlig sesongmønster
- Husstandens DNA: varene som er med i over 40 % av ordrene
- Matkultur (norsk / italiensk / asiatisk / tex-mex / indisk),
  kokestil (råvarer vs. ferdigmat), prisbevissthet og helsesignaler
- Drikkeprofil, topp produkter og kategorier siste 12 måneder
- Sesongprodukter og året i Oda-måneder (sommerferie-gapet)
- Basket-analyse: hvilke varer havner ofte sammen, med et
  "når jeg kjøper X, hva følger med?"-søk

## Innlogging mot Oda

Tre kilder, i prioritert rekkefølge:

1. **Manuell cookie i `.env`**: kopier `.env.example` til `.env` og lim
   inn `sessionid` og `csrftoken` fra DevTools (`F12` → Storage →
   Cookies → `https://oda.com`).
2. **Brukernavn og passord**: sett `ODA_USERNAME` og `ODA_PASSWORD` i
   `.env`, så logger appen inn selv og buffrer sesjonen i
   `data/session.json`. Nødvendig på servere uten nettleser. Bevisst
   litt skjørt: login-endepunktet er udokumentert, og captcha eller 2FA
   fra Oda vil knekke det (synlig i loggen).
3. **Nettleser-cookie** (default lokalt): leses automatisk fra Firefox,
   Chrome, Safari, Edge, Brave, Arc, Opera, Vivaldi, Chromium eller
   LibreWolf via `rookiepy`. Første med gyldig session vinner, overstyr
   med `ODA_BROWSER=firefox`. På macOS ber Chrome-familien om
   Keychain-tilgang første gang, Firefox spør ikke.

## Kjøre på egen server

`docker-compose.truenas.yml` er en frittstående stack for hjemmeserver
bak NAT: ferdigbygget image fra GHCR, Cloudflare Tunnel for tilgang
utenfra uten portåpning, Watchtower for auto-deploy ved push til `main`,
og app-passord (`APP_PASSWORD` i `.env`) foran hele appen. Full guide i
[DEPLOY.md](DEPLOY.md).

Ingenting i appen krever det oppsettet. Den er en vanlig FastAPI-app på
port 8000 og kan like gjerne stå bak en annen reverse proxy. Ved fork
bygger CI automatisk til ditt eget GHCR-navnerom
(`ghcr.io/<bruker>/<repo>`), sett `ODA_IMAGE` i `.env` så compose-filene
bruker det.

## Under panseret

To lag: en datapipeline som lagrer ordrehistorikk som CSV under `data/`
(gitignored), og FastAPI + HTMX oppå. All analyse er ren pandas uten
I/O, se `CLAUDE.md` for arkitekturen.

Tvinge en henting fra terminalen:

```sh
uv run python -m min_oda.fetch_orders
```

Treffer ikke de antatte endepunktene lenger, finn riktig URL i
DevTools (Network-fanen på Mine ordrer-siden) og kjør
`uv run python -m min_oda.fetch_orders --url '<URL>'`.

Tester:

```sh
uv run pytest
```

Dekker data-algoritmene (kadens, kurv-diff, kuratering, varetype-
klassifisering) og en smoke-test av web-rutene. Web-testene hopper over
hvis `data/orders.csv` mangler, det er forventet i en fersk klone.

## Personvern

- `.env` og `data/` er gitignored: cookies, sesjoner og handlehistorikk
  havner aldri i git.
- Credentials sendes kun til oda.com. Nettleser-cookien leses lokalt
  fra nettleserens egen cookie-database.
- Får du 401/403 er cookien utløpt: logg inn på oda.com igjen og
  trykk `⟳`.
