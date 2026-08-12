# Deploy på TrueNAS (Dockge + Cloudflare + auto-oppdatering)

Kjør min-oda som en Dockge-stack på TrueNAS, med Cloudflare Tunnel foran så
den er tilgjengelig utenfra uten å åpne porter, et app-passord så bare du
(og kona) kommer inn, og Watchtower så en `git push` deployer seg selv.

Tre containere i én stack (`docker-compose.truenas.yml`):

- `min-oda` — appen. Hentes ferdigbygget fra GHCR (ingen bygg på NAS-en).
  Logger inn mot Oda selv med brukernavn/passord og buffrer sesjonen i
  volumet. Henter fersk ordrehistorikk ved oppstart og hver 24. time.
- `cloudflared` — Cloudflare Tunnel. Ringer ut til Cloudflare, så det trengs
  ingen portåpning eller NAT på hjemmenettet.
- `watchtower` — poller GHCR og recreater `min-oda` når `:latest` endres.

Flyten: `git push origin main` → GitHub Actions bygger og pusher imaget til
GHCR → Watchtower henter det ned og recreater containeren. Helt hands-off.

## Forutsetninger

- Et domene lagt til i Cloudflare (gratis-plan holder).
- Tilgang til å opprette en Cloudflare Tunnel (Cloudflare One / Networks ->
  Tunnels). Ingen Zero Trust Access nødvendig — tilgangskontrollen er app-
  passordet (steg 5).
- TrueNAS SCALE med Dockge (samme oppsett som de andre appene dine).

## 1. Gjør GHCR-imaget hentbart

GitHub Actions bygger `ghcr.io/mhellevang/min-oda:latest` ved hver push til
`main` (se `.github/workflows/build.yml`). Første gang: kjør workflowen én gang
(push til main, eller **Actions -> Build image -> Run workflow**), så imaget
finnes i GHCR.

Enklest for at NAS-en skal få hentet det uten innlogging: gjør pakken offentlig.
På GitHub -> profil/repo -> **Packages** -> `min-oda` -> **Package settings** ->
**Change visibility** -> **Public**. Imaget inneholder ingen hemmeligheter
(credentials ligger i `.env` på NAS-en i runtime), så dette er trygt.

(Vil du heller holde pakken privat, må Watchtower få GHCR-credentials via en
`config.json` montert inn, det er mer arbeid. Offentlig pakke er enklest.)

## 2. Lag stacken i Dockge

Ingen git-klone på NAS-en. I Dockge: lag en ny stack `min-oda`, lim inn
innholdet i `docker-compose.truenas.yml`, og lag en `.env` i samme
stack-katalog:

```env
# Oda-innlogging (appen logger inn selv)
ODA_USERNAME=din@epost.no
ODA_PASSWORD=ditt-oda-passord

# Cloudflare Tunnel (fylles inn i steg 3)
CLOUDFLARE_TUNNEL_TOKEN=

# App-passord foran hele appen (delt passord for deg + kona)
APP_PASSWORD=et-langt-delt-passord
SESSION_SECRET=en-annen-lang-tilfeldig-streng
COOKIE_SECURE=true

# LLM-forslag (valgfritt, se steg 7). none slår funksjonene av.
LLM_PROVIDER=auto
```

Se `.env.example` for alle tilgjengelige variabler. `.env` blir aldri en del
av imaget, den leses i runtime.

## 3. Opprett Cloudflare Tunnel

1. Gå til Zero Trust -> **Networks** -> **Tunnels** -> **Create a tunnel**.
2. Velg **Cloudflared** som connector-type. Gi den et navn (f.eks. `min-oda`).
3. Cloudflare viser en installasjonskommando med et langt token. Du trenger
   bare selve tokenet (strengen etter `--token`). Lim det inn i `.env`:

   ```
   CLOUDFLARE_TUNNEL_TOKEN=eyJ...langt-token...
   ```

   Ikke kjør installasjonskommandoen fra dashboardet, `cloudflared`-containeren
   i stacken gjør jobben.

## 4. Koble hostname til appen

Fortsatt i tunnel-oppsettet, under **Public Hostnames** -> **Add a public
hostname**:

- **Subdomain**: f.eks. `oda`
- **Domain**: ditt domene (blir `oda.dittdomene.no`)
- **Type**: `HTTP`
- **URL**: `min-oda:8000`

`min-oda:8000` fungerer fordi `cloudflared` kjører i samme Docker-nettverk som
appen og slår opp containernavnet internt. Appen selv publiserer ingen porter.

## 5. Lås tilgangen til deg og kona

Cloudflare Access krever Zero Trust, som ikke er tilgjengelig på en ren
gratis-konto uten egen onboarding. I stedet ligger tilgangskontrollen i appen
selv: et delt passord (`APP_PASSWORD`) foran hele appen, satt i `.env` (steg 2).

Slik virker det:

- Er `APP_PASSWORD` satt, må enhver logge inn på `/login` før de når noe som
  helst. Innlogging gir en signert HMAC-cookie (30 dagers levetid).
- `SESSION_SECRET` signerer cookien (kan være hva som helst langt og tilfeldig).
- `COOKIE_SECURE=true` gjør at cookien kun sendes over HTTPS (som er tilfellet
  bak Cloudflare Tunnel).
- Er `APP_PASSWORD` tomt, er auth av og alt er åpent (greit lokalt).

Samme modell som avisa. Vil du senere ha ekte SSO/e-postbasert Access i stedet,
kan du aktivere Zero Trust og legge en Access-applikasjon foran hostnamet, men
app-passordet er tilstrekkelig for et par brukere.

## 6. Deploy i Dockge

Trykk **Deploy** (eller **Start**) på stacken. Dockge henter imaget fra GHCR
(ingen bygging), starter appen, `cloudflared` og `watchtower`. Fra
kommandolinjen tilsvarer det:

```sh
docker compose -f docker-compose.truenas.yml up -d
```

Ved oppstart logger appen seg inn mot Oda og henter ordrehistorikken. Åpne
`https://oda.dittdomene.no`, logg inn via Cloudflare, og appen skal svare.

## 7. Logg inn Codex (valgfritt, for LLM-forslag)

Imaget har `codex`-CLI-en innebygd, og forslags-funksjonene på handlelista
(sparetips + nye varer) bruker Codex-abonnementet. Engangs-innlogging inne i
containeren:

```sh
docker compose -f docker-compose.truenas.yml exec min-oda \
  codex login -c 'cli_auth_credentials_store="file"' --device-auth
```

Følg device-lenken i utskriften og logg inn med ChatGPT-kontoen. Innloggingen
lagres i det egne volumet `codex-auth` (`CODEX_HOME=/var/lib/codex`) og
overlever image-oppdateringer. Behandle volumet som en hemmelighet.

Uten innlogging (eller med `LLM_PROVIDER=none`) forsvinner bare
forslags-seksjonen fra UI-et, resten av appen er uendret. Sjekk status med
`... exec min-oda codex login status`.

## Oppdatering (auto)

`git push origin main` bygger og pusher et ferskt
`ghcr.io/mhellevang/min-oda:latest` via GitHub Actions. NAS-en sitter bak NAT,
så CI kan ikke pushe oppdateringen inn, men `watchtower` poller GHCR hvert 5.
minutt og recreater `min-oda` når `:latest` endres. En push når altså boksen
av seg selv.

Watchtower er scoped med `com.centurylinklabs.watchtower.enable=true` på
`min-oda` (pluss `WATCHTOWER_LABEL_ENABLE=true`), så den rører kun denne
stacken, ikke andre containere på NAS-en.

> **Kun ved første deploy:** Watchtower begynner å oppdatere *etter* at stacken
> er oppe, så aller første gang du endrer selve compose-fila må du gjøre en
> manuell **Pull + Up** i Dockge for å få inn `watchtower`-tjenesten. Deretter
> er det hands-off.

Avveining: en dårlig push auto-deployer. Lav innsats for en personlig app, men
vil du heller godkjenne hver oppdatering, fjern `watchtower`-tjenesten og bruk
**Pull + Up** i Dockge manuelt.

## Drift

- **Logg**: `docker compose -f docker-compose.truenas.yml logs -f min-oda`
  (eller logg-fanen i Dockge). Se etter `passord` som auth-kilde og at
  hentingen går gjennom.
- **Sesjon utløper**: sessionid lever ~30 dager. Appen buffrer den i
  `data/session.json` (i volumet) og logger automatisk inn på nytt når en
  henting feiler. Trenger du å tvinge ny innlogging, slett `session.json` fra
  volumet og restart.
- **Oda krever captcha/2FA**: da feiler passord-login (synlig i loggen). Fall
  tilbake på manuell cookie: sett `ODA_SESSIONID` (og `ODA_CSRFTOKEN`) i `.env`
  fra en innlogget nettleser, den kilden vinner over passord-login.

## Data og backup

Alt appen produserer ligger i det navngitte volumet `min-oda-data` (montert på
`/app/data`): `orders.csv`, `lines.csv`, ordre-JSON, blocklist og den bufrede
sesjonen. Watchtower beholder volumet når den oppdaterer containeren. Ordre-data
kan hentes på nytt fra Oda, men blocklist (`blocklist.json`) er din egen tilstand
verdt å ta backup av. Den committede `data/product_types.json` sås automatisk
inn i et tomt volum ved oppstart.
