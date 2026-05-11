# Oda-analyse

Personlig analyse av handlehistorikk fra oda.com.

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
uv run fetch_orders.py
```

Hvis ingen av de antatte endepunktene treffer, finn riktig URL i DevTools:

1. Åpne `oda.com` → **Min konto** → **Mine ordrer** med F12 oppe.
2. Network-fanen, filtrer XHR/Fetch.
3. Finn en request som returnerer JSON med ordrelisten.
4. Høyreklikk → Copy → Copy URL.
5. Kjør `uv run fetch_orders.py --url '<URL>'`.

Rådata lagres i `data/` (gitignored).

## Analyser

```sh
uv run analyze.py
```

Skriver `orders.csv` og `lines.csv`, og skriver oversikt til terminalen:
totaler, månedsfordeling, top produkter, kategorier, merker.

```sh
uv run portrait.py
```

Livsstilsanalyse: barnefase, kjøkken (norsk/italiensk/tex-mex/asiatisk),
prisbevissthet, kokestil, helse-signaler, drikke, faste varer.

```sh
uv run seasonality.py
uv run seasonality.py --cutoff 2025-10-01 --label "Flytting"
```

Sesongmønstre: ekte sesongprodukter (kjøpt kun visse måneder), hvilke
kategorier topper hvilke måneder, og sommerferie-gapet. Lager også
`plots/monthly_spend.png`. Med `--cutoff` får du en før/etter-sammenligning
rundt en valgt dato (livshendelse, flytting, etc.) og en markør på grafen.

```sh
uv run prices.py
uv run prices.py --ssb              # sammenlign mot SSB KPI matvarer
uv run prices.py --since 2022 --top 30
```

Prisanalyse: personlig matprisindeks per kvartal (veid Carli — hvert produkt
ankret til sin egen første-pris), per-produkt prisutvikling med største opp-
og nedganger, og MVA-mix (15% mat vs 25% non-food) per år. Med `--ssb` hentes
månedlig KPI for matvarer fra SSB (tabell 03013) og sammenlignes — godt
egnet til å se om din kurv har bevegd seg annerledes enn snittet i Norge.
Lagrer `plots/price_index.png`.

## Lag handleliste på oda.com

```sh
uv run build_list.py            # forhåndsvisning
uv run build_list.py --create   # opprett listen på oda.com
```

Bygger en kuratert liste med faste varer basert på handlemønsteret siste
12 mnd. Filtrerer bort størrelses-kodede produkter (bleier, melk-trinn,
babymat 4/6/8 mnd) som ikke er kjøpt siste ~4 mnd, slik at utvokste
størrelser ikke havner på listen. Bruk `--title "..."` for å velge navn.

## Sikkerhet

- `.env` og `data/` er gitignored — cookies og personlig handlehistorikk
  forlater aldri maskinen.
- Session-cookien er den eneste credentialen som brukes — passordet ditt
  rører vi ikke.
- Cookien utløper etter en stund; logg inn på nytt og oppdater `.env` om
  du får 401/403.
