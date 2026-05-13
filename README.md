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

```sh
uv run basket.py
uv run basket.py --product "kokosmelk"
uv run basket.py --min-orders 8 --top 25
```

Basket-analyse: hvilke produkter havner ofte i samme ordre? Viser topp par
etter *lift* (mest overraskende kombinasjoner) og *støtte* (mest vanlige
kombinasjoner). Med `--product` får du et oppslag for ett spesifikt produkt
— "når jeg kjøper X, hva følger med?". `--min-orders` styrer hvor sjeldne
produkter må filtreres bort før de inngår i parene.

```sh
uv run restock.py
uv run restock.py --horizon 7
uv run restock.py --by-product          # drill ned til produkt-id
uv run restock.py --min-buys 4 --max-median 60
uv run restock.py --all
```

Restock-forslag: aggregerer kjøp per varetype (brød, melk, ost, …) og
beregner median-intervall mellom kjøp + forventet neste-kjøp-dato.
Viser hva som *snart* går tomt (status `forfalt`/`akkurat nå`/`snart`)
sortert etter hvor lenge siden det skulle vært handlet. Varetyper med
median over `--max-median` (90 dager som standard) regnes som sjeldne
kjøp og droppes. Det samme gjelder varetyper som ikke er kjøpt på
lenge — de regnes som forlatt. `--all` viser alle uansett. CV-kolonnen
er variasjonskoeffisient (lave verdier = pålitelig kadens).

Klassifiseringen ligger i `product_types.py` med en eksplisitt mapping
i `data/product_types.json` for de hyppigst kjøpte produktene, og en
keyword-fallback for resten. Med `--by-product` får du drill-down til
konkrete produkt-id-er i stedet for varetype-aggregering.

```sh
uv run report.py
uv run report.py --no-ssb --out min-rapport.html
```

Samler de viktigste analysene i én selvstendig HTML-fil med nøkkeltall,
månedlig forbruk, restock-forslag, topp-produkter/-kategorier siste 12
mnd, prisindeks (med SSB-sammenligning) og sesongprodukter. Plot bakes
inn som base64, så filen kan deles uten støtte-filer.

## Lag handleliste på oda.com

```sh
uv run build_list.py                       # forhåndsvisning
uv run build_list.py --create              # opprett listen på oda.com
uv run build_list.py --cycle 7             # ukentlig syklus (default 14 d)
uv run build_list.py --max-per-category 12 # mer plass i kategorier med
                                           # mange staples (f.eks. Meieri)
```

Bygger på `restock.compute_cadence(by_type=True)`: for hver varetype med
stabil kjøpsrytme velges produktet med flest distinkte ordrer som
representant, og antall settes til `ceil(syklus / median-intervall)`.
Melk med 7-dagers kadens får qty=2 på en 14-dagers liste; brød med
5-dagers kadens får qty=3.

Arver filtrene fra restock: pant/gavekort, forlatte produkter, sjeldne
kjøp (median > 90 d), og størrelses-kodede varer som vokses ut av.

## Diff mot handlekurv

```sh
uv run cart_diff.py             # vis varetyper som mangler i kurven
uv run cart_diff.py --top-up    # ta også med varer i kurv, men for lavt antall
uv run cart_diff.py --create    # opprett liste med manglene
```

Sammenligner build_list-resultatet mot innholdet i kurven på oda.com
(`/api/v1/cart/`) og foreslår hva som mangler. Snittet skjer på
varetype-nivå — har du allerede TINE Lettmelk i kurven regnes melk-
behovet som dekket selv om build_list foreslo et annet merke. Det
forhindrer falske mangler ved merkebytte.

## GUI

```sh
make web                       # FastAPI + HTMX-app (anbefalt) på :8000
make gui                       # eller Streamlit-app som alternativ
```

To valg:

**`make web`** (FastAPI + HTMX, anbefalt): polert visuell stil arvet fra
report.html (kremhvit bakgrunn, slate-blå aksent, kortlayout). Én
side, `/handleliste`, med to moduser: default bygger fersk ukehandel,
toggelen *Legg til kurv* sammenligner med kurven på oda.com og viser
kun det som mangler. Tabellen har status-pills (forfalt/snart/i rute)
og "forfaller om X d" per rad, redigerbare antall-felt, og HTMX-drevet
filtrering uten page reload.

**`make gui`** (Streamlit): eldre alternativ. Litt mer "out of the box"
men mindre kontroll over utseende — kommer til å fases ut etter hvert.

Begge gjenbruker samme analyse-logikk (`build_list.curate`,
`cart_diff.compute_diff`, `restock.compute_cadence`) — det er kun det
visuelle laget som skiller.

## Makefile

```sh
make refresh   # hent nye ordrer fra Oda
make report    # generer HTML-rapport
make tables    # alle terminal-analyser
make web       # start FastAPI + HTMX-app
make gui       # start Streamlit-app (alternativ)
make all       # refresh + report
```

`build_list` og `cart_diff` er bevisst utelatt fra `make tables` siden de
har eksterne sideeffekter (oppretter lister på oda.com).

## Sikkerhet

- `.env` og `data/` er gitignored — cookies og personlig handlehistorikk
  forlater aldri maskinen.
- Session-cookien er den eneste credentialen som brukes — passordet ditt
  rører vi ikke.
- Cookien utløper etter en stund; logg inn på nytt og oppdater `.env` om
  du får 401/403.
