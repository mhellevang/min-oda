"""Klassifiserer Oda-produkter til en varetype (brød, melk, ost, …).

Bruker en eksplisitt mapping i `data/product_types.json` for produkter
vi har klassifisert manuelt. For ukjente produkter brukes en
keyword-fallback over produktnavnet, og som siste utvei en grovere
mapping av Oda-kategorien. Returnerer None hvis ingen regel treffer.

Bruk:
    from product_types import product_type
    t = product_type("Korn Bakeri Solsikkebrød 620 g", "Bakeri og konditori")
    # → "brød"
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
MAPPING_FILE = DATA_DIR / "product_types.json"


@lru_cache(maxsize=1)
def _explicit_mapping() -> dict[int, str]:
    if not MAPPING_FILE.exists():
        return {}
    raw = json.loads(MAPPING_FILE.read_text())
    return {int(k): v for k, v in raw.items()}


# Mer spesifikke regler øverst. Hver regel = (kompilert regex, type).
# Reglene matches mot lowercase produktnavn.
_KEYWORD_RULES: list[tuple[re.Pattern[str], str]] = [
    # Baby — sjekk først så "babymat" ikke trigges av annet
    (re.compile(r"bleie"), "bleier"),  # ingen \b — «buksebleier» er også bleier
    (re.compile(r"våtserviett"), "våtservietter"),
    (re.compile(r"stellekluter"), "stellekluter"),
    (re.compile(r"morsmelkerstat|\bnan\b|sensilac|nan pro|nan 1|nan 2"), "morsmelkerstatning"),
    (re.compile(r"\bgrogro\b|babymat|fra \d+ ?mnd|barnegrøt"), "babymat"),
    (re.compile(r"barnetannkrem"), "tannkrem"),

    # Bakeri (brød kommer før "muslibrød" osv. ved at vi tester "brød"
    # som suffix). Pizzabunn skiller seg fra brød.
    (re.compile(r"pizzadeig|pizzabunn|paibunn"), "pizzadeig"),
    (re.compile(r"hamburgerbrød|burgerbrød"), "rundstykker"),
    (re.compile(r"naan|pita|tortilla|wrap|lompe|lefs[ae]|bagel"), "tortilla-lompe"),
    (re.compile(r"rundstykk|focaccia|miniflute|baguette|surdeig"), "rundstykker"),
    (re.compile(r"brød|loff|knekkebrød"), "brød"),

    # Pasta / ris / nudler
    (re.compile(r"pasta|spaghetti|fusilli|penne|tagliatelle|makaroni|risotto"), "pasta"),
    (re.compile(r"nudler|noodles|ramen|pad thai|miehoen"), "nudler"),
    (re.compile(r"\bris\b|jasminris|basmati|fullkornsris|middagsris|fullkornris"), "ris"),

    # Meieri
    (re.compile(r"havredrikk|mandeldrikk|soyadrikk|barista edition"), "plantedrikk"),
    (re.compile(r"\bmelk|tinemelk|skummet|helmelk|lettmelk|sjokolademelk|mjølk"), "melk"),
    (re.compile(r"\bskyr\b"), "skyr"),
    (re.compile(r"\bkefir"), "kefir"),
    # Yoghurt-undertyper: barn (90g junior) og gresk/tyrkisk naturell er
    # genuint forskjellige fra vanlig frokostyoghurt. Gresk og tyrkisk
    # regnes som byttbare. Må komme før den generelle yoghurt-regelen.
    (re.compile(r"junior yoghurt|barneyoghurt"), "yoghurt-junior"),
    (re.compile(r"gresk yoghurt|yoghurt gresk|tyrkisk yoghurt|yoghurt tyrkisk"), "yoghurt-gresk-tyrkisk"),
    (re.compile(r"yoghurt"), "yoghurt"),
    (re.compile(r"crème fraîche|creme fraiche"), "crème-fraîche"),
    (re.compile(r"rømme"), "rømme"),
    (re.compile(r"matfløte|kremfløte|fløte"), "fløte"),
    (re.compile(r"cottage cheese"), "cottage-cheese"),
    (re.compile(r"mozzarella|halloumi|fetaost|fetacheese|apetina"), "mozzarella"),
    (re.compile(r"philadelphia|snøfrisk|chevre|brie|camembert|smøreost"), "kremost"),
    (re.compile(r"parmigiano|parmesan|grana padano"), "ost"),
    (re.compile(r"jarlsberg|gulost|brunost|gudbrandsdal|ridderost|cheddar|revet ost|gräddost"), "ost"),
    (re.compile(r"smør\b|meierismør|bremykt"), "smør"),
    (re.compile(r"soft flora|olivero"), "margarin"),
    (re.compile(r"\begg\b|frokostegg|solegg"), "egg"),

    # Kjøtt og fisk
    (re.compile(r"karbonadedeig|kjøttdeig|kvernet deig"), "kjøttdeig"),
    (re.compile(r"kjøttkaker|kjøttboller|kyllingkjøttboller"), "kjøttkaker"),
    (re.compile(r"\bbacon\b|stjernebacon"), "bacon"),
    (re.compile(r"wienerpølse|grillpølse|kjøttpølse|festwiener"), "pølse"),
    (re.compile(r"leverpostei"), "leverpostei"),
    (re.compile(r"salami|chorizo|pepperoni|spekeskinke|italiana"), "spekemat"),
    (re.compile(r"kalkun"), "kalkun"),
    (re.compile(r"kokt skinke|edel skinke|skinke|bogskinke|kalkunskinke|strandaskinke|hamburgerrygg"), "skinke"),
    (re.compile(r"kyllingfilet|kyllinglår|lårbiff|lårfilet|kyllinglårklubber|hel kylling|landkylling|kylling"), "kylling"),
    (re.compile(r"butchers cut|svinekoteletter|nakkekoteletter|lammebog|lammestek|lammelår|ytrefilet av lam|biff"), "kjøtt-stykke"),
    (re.compile(r"hamburger\b"), "hamburger"),
    (re.compile(r"røkt laks|røkt lakse|laks i skiver|røkt laks"), "laks-røkt"),
    (re.compile(r"laksefilet|torskefilet|lakse"), "fisk-fersk"),
    (re.compile(r"fiskekaker|fiskepinner|fiskegrateng|fiskefilet|sprø torsk|sprøbakt"), "fiskekaker"),
    (re.compile(r"makrell|kaviar"), "makrell"),
    (re.compile(r"matpakkekaker"), "fiskekaker"),

    # Pålegg (søtt og annet)
    (re.compile(r"syltetøy|eplemos|tyttebær"), "syltetøy"),
    (re.compile(r"peanut\s?butter|peanøttsmør"), "peanøttsmør"),
    (re.compile(r"majones|majo\b|hellmann"), "majones"),
    (re.compile(r"hummus"), "hummus"),
    (re.compile(r"sennep|dijonsennep|bodsennep"), "sennep"),
    (re.compile(r"ketchup"), "ketchup"),
    (re.compile(r"tabasco|sriracha"), "hot-saus"),

    # Sammensatte/foredlede varer som inneholder fruktnavn — må komme
    # før de rene fruktreglene så ikke f.eks. "banan-müsli" ender som banan
    # eller "Farris bris mango & papaya" ender som mango.
    (re.compile(r"\bmüsli|musli\b|granola|cornflakes|fitness|fruktmusli"), "frokostblanding"),
    (re.compile(r"smoothie\b"), "smoothie"),
    (re.compile(r"appelsinjuice|eplejuice|tranebærjuice|frokostjuice|juice|sitronjuice"), "juice"),
    (re.compile(r"pepsi|coca-cola|solo|sprite|farris bris|hamar julebrus|julebrus"), "brus"),

    # Frukt og grønt
    (re.compile(r"\bagurk"), "agurk"),
    (re.compile(r"avokado"), "avokado"),
    (re.compile(r"banan"), "banan"),
    (re.compile(r"blåbær|jordbær|bringebær|bjørnebær|bærblanding|smoothieblanding|tranebær|rips|solbær"), "bær"),
    (re.compile(r"druer"), "druer"),
    (re.compile(r"\beple|granny|pink lady\b"), "eple"),
    (re.compile(r"fersken|nektarin"), "fersken"),
    (re.compile(r"grapefrukt"), "grapefrukt"),
    (re.compile(r"kiwi"), "kiwi"),
    (re.compile(r"klementin|appelsin|mandarin"), "klementin-appelsin"),
    (re.compile(r"sitron|lime"), "sitron-lime"),
    (re.compile(r"\bmango"), "mango"),
    (re.compile(r"melon|vannmelon"), "melon"),
    (re.compile(r"papaya"), "papaya"),
    (re.compile(r"persimon"), "persimon"),
    (re.compile(r"plommer"), "plomme"),
    (re.compile(r"\bpærer|pære\b"), "pære"),
    (re.compile(r"tomatpur[eé]"), "tomatpuré"),
    (re.compile(r"knuste tomater|hakkede tomater|grovhakkede tomater|tomater hakkede|tomater grovhakkede|soltørkede tomater"), "tomat-konserv"),
    (re.compile(r"klasetomat|cherrytomat|plommetomat|miljøgartneriet tomater|små.+tomater|hverdagstomater|dulcita|tomater|wiig gartneri tomat|mini plomme"), "tomat-fersk"),
    (re.compile(r"sjalottløk|vårløk|purreløk"), "løk"),
    (re.compile(r"\bløk\b|gul løk|rødløk|kinesisk hvitløk"), "løk"),
    (re.compile(r"hvitløk"), "hvitløk"),
    (re.compile(r"paprika|spisspaprika"), "paprika"),
    (re.compile(r"chili|chilipepper"), "chili"),
    (re.compile(r"ingefær"), "ingefær"),
    (re.compile(r"gulrot|gulrøtter|snacksgulrot"), "gulrot"),
    (re.compile(r"brokkoli"), "brokkoli"),
    (re.compile(r"blomkål|blomkålris"), "blomkål"),
    (re.compile(r"kinakål|grønnkål|hodekål|pak choi|råkostmiks"), "kål"),
    (re.compile(r"\bsalat|hjertesalat|ruccola|babyleaf|crispisalat|salatmiks|salatblanding"), "salat"),
    (re.compile(r"spinat"), "spinat"),
    (re.compile(r"sellerirot|stangselleri"), "selleri"),
    (re.compile(r"squash"), "squash"),
    (re.compile(r"aubergine"), "aubergine"),
    (re.compile(r"søtpotet"), "søtpotet"),
    (re.compile(r"poteter|potet|fløtepoteter"), "potet"),
    (re.compile(r"\bmais\b|maiskorn|maiskolb"), "mais"),
    (re.compile(r"sukkererter|aspargesbønner|edamame"), "aspargesbønner"),
    (re.compile(r"\berter\b"), "erter"),
    (re.compile(r"(?<!kaffe)bønner|kidneybønner|sorte bønner|hvite bønner|bønnespirer"), "bønner-konserv"),
    (re.compile(r"linser|røde linser"), "linser"),
    (re.compile(r"kikerter"), "kikerter"),
    (re.compile(r"champignon|aromasopp|sopp\b"), "sopp"),
    (re.compile(r"\bbasilikum|kruspersille|koriander|bladpersille|\bdill\b|rosmarin|laurbær"), "urter"),
    (re.compile(r"mandler|cashewnøtter|peanøtter|valnøtter|hasselnøt|nøtte|fruktmix|nøtter"), "nøtter"),
    (re.compile(r"svisker|aprikoser|chiafrø|gresskarkjerner|kokosflak|kokosmasse|tranebær tørket"), "tørket-frukt-frø"),
    (re.compile(r"reddik"), "reddik"),
    (re.compile(r"alfalfaspirer|bambusskudd|sylteagurker|ananas"), "grønnsak-konserv"),
    (re.compile(r"smoothie\b"), "smoothie"),
    (re.compile(r"fruktkurv"), "frukt-blanding"),
    (re.compile(r"wokmiks|wok classic"), "wok-miks"),

    # Mel / bakeing
    (re.compile(r"hvetemel|rugmel|spelt|landhvete|kornbrød 1-2-3|havrebrød 1-2-3|pizzamel|sammalt"), "mel"),
    (re.compile(r"havre steelcut|havre sammalt"), "havre"),
    (re.compile(r"tørrgjær|bakegjær"), "gjær"),
    (re.compile(r"bakepulver"), "bakepulver"),
    (re.compile(r"vaniljeessens|vaniljekesam"), "bakeing-annet"),
    (re.compile(r"kokesjokolade"), "kokesjokolade"),
    (re.compile(r"\bmelis\b|brunt sukker|\bsukker\b"), "sukker"),

    # Frokost
    (re.compile(r"musli|müsli|granola|cornflakes|fruktmusli|fitness"), "frokostblanding"),
    (re.compile(r"havregryn|risgrøt|proteinringer|proteinpulver"), "havre"),

    # Drikke
    (re.compile(r"kaffekapsel|kapsler"), "kaffe-kapsler"),
    (re.compile(r"hele bønner|kaffebønner"), "kaffe-bønner"),
    (re.compile(r"filtermalt|brente bønner|kaffe"), "kaffe"),
    (re.compile(r"earl grey|\bte\b"), "te"),
    (re.compile(r"farris|snåsavann|naturell vann"), "vann"),
    (re.compile(r"\bøl\b|juleøl|pilsner|fatøl|radler|ipa|nastro azzurro"), "øl"),
    (re.compile(r"sjokoladedrikk|regia"), "sjokoladedrikk"),
    (re.compile(r"restitusjonsdrikk"), "sportsdrikk"),

    # Tilbehør / krydder / sauser
    (re.compile(r"buljong"), "buljong"),
    (re.compile(r"kokosmelk"), "kokosmelk"),
    (re.compile(r"olivenolje|soyaolje|olje"), "olje"),
    (re.compile(r"eddik"), "eddik"),
    (re.compile(r"soyasaus|ketjap manis|teriyaki|pad thai|tikka masala|karrisaus|wok-saus|currysaus|pesto|tomatsaus|tomatsauce|bbq sauce|sweet & spicy"), "saus"),
    (re.compile(r"pizzasaus"), "saus"),
    (re.compile(r"tomatsuppe|suppe|rett i koppen"), "suppe"),
    (re.compile(r"taco|salsa|nachips|tortillachips"), "taco"),
    (re.compile(r"krydder|pepper|tacokrydder|chiliflak|kanel|\-rub\b"), "krydder"),
    (re.compile(r"sprøstekt løk"), "sprøstekt-løk"),
    (re.compile(r"hvitløksbaguett"), "tortilla-lompe"),

    # Snacks og søtt
    (re.compile(r"sørlandschips|chips|popcorn|saltsten|tortillachips"), "snacks"),
    (re.compile(r"kvikk lunsj|smash|firkløver|melkesjokolade|premium dark|helnøtt|twist|melkehjerter|kinder|nidar|brente mandler"), "sjokolade"),
    (re.compile(r"sur\b|drops|tutti frutti|minimix|non stop|fazer"), "godteri"),
    (re.compile(r"iskrem|krone-is|sorbet|ispinne|båt-is|lollipop|sjokoladepudding|proteinpudding|sandwich"), "iskrem-dessert"),
    (re.compile(r"figurpepperkaker|pepperkaker"), "kjeks"),

    # Ferdigmat / restauranter
    (re.compile(r"fjordland|grandiosa|biffgryte|svenske kjøttboller|stroganoff|biff stroganoff"), "ferdigmat"),
    (re.compile(r"falafel|plantego"), "plantebasert"),
    (re.compile(r"vafler"), "vafler"),

    # Hus og hjem
    (re.compile(r"toalettpapir"), "toalettpapir"),
    (re.compile(r"kjøkkenrull|tørkerull"), "kjøkkenrull"),
    (re.compile(r"tøyvask|vaskepulver|vaskekapsler|vaskeark|omo|milo|neutral"), "tøyvask"),
    (re.compile(r"tøymykner|comfort"), "tøymykner"),
    (re.compile(r"zalo|sun tabs|sun sun basic|oppvaskmiddel|oppvask"), "oppvaskmiddel"),
    (re.compile(r"avfallspose|kildesortering"), "avfallspose"),
    (re.compile(r"jif|klorin|allrent|baderom|engangsvåtmopp|rengjøringsservietter|rengjøringsspray"), "rengjøring"),
    (re.compile(r"\bklor\b"), "rengjøring"),
    (re.compile(r"batterier|batteri "), "batterier"),
    (re.compile(r"bakepapir|fyrstikker|peiskubbe|stivelse|vindusnal|refleksbånd|opptenning"), "hus-annet"),
    (re.compile(r"infinitum|panteflasker"), "pant"),

    # Hygiene
    (re.compile(r"shampoo"), "shampoo"),
    (re.compile(r"håndsåpe|palmolive"), "håndsåpe"),
    (re.compile(r"tannkrem|tannbørste|solidox|sensodyne|zendium"), "tannkrem"),
    (re.compile(r"deo\b|deodorant|rollon"), "deodorant"),
    (re.compile(r"barbergel|barberblad|fusion|venus"), "barbering"),
    (re.compile(r"vaseline|barnoil|baby bee"), "hudpleie"),

    # Helse / andre
    (re.compile(r"paracet|stikkpille|trankapsler"), "legemidler"),
    (re.compile(r"fairtrade roser|tulipaner|grennellik|valentine|blomster|bukett"), "blomster"),
    (re.compile(r"hundepose"), "kjæledyr"),
    (re.compile(r"sprell|limstift"), "kontor-leke"),
    (re.compile(r"matskjeer"), "spise-utstyr"),
]


# Størrelses-kode → suffiks. Bleier, babymat, morsmelkerstatning og lignende
# kommer i sjikt som ikke er substituerbare for husholdninger med barn i
# ulike aldre samtidig (treåring i str. 6, tvillinger i str. 3). Suffikset
# splitter varetypen så hver størrelse får sin egen rytme og representant.
# Patterns testes i rekkefølge — Str./Trinn./N mnd foretrekkes over kg-rangen
# fordi de koder størrelsen direkte (bleier har typisk begge: "Str. 5, 12-25kg").
_SIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bstr\.?\s*(\d+)", re.IGNORECASE), "str{0}"),
    (re.compile(r"\btrinn\s*(\d+)", re.IGNORECASE), "trinn{0}"),
    (re.compile(r"\bfra\s*(\d+)\s*mnd\b", re.IGNORECASE), "{0}mnd"),
    (re.compile(r"\b(\d+)\s*mnd\b", re.IGNORECASE), "{0}mnd"),
    (re.compile(r"\b(\d+)\s*-\s*(\d+)\s*kg\b", re.IGNORECASE), "{0}-{1}kg"),
]


def _extract_size_suffix(name: str) -> str | None:
    """Plukk ut et kanonisk størrelses-suffiks ('str5', 'trinn3', '6mnd',
    '12-25kg') fra et produktnavn, eller None hvis ingen størrelseskode finnes.

    Brukes til å skille varianter av samme varetype som ikke er substituerbare
    (bleier i str. 3 dekker ikke et behov for str. 6).
    """
    for pattern, fmt in _SIZE_PATTERNS:
        m = pattern.search(name)
        if m:
            return fmt.format(*m.groups()).lower()
    return None


# Kategorier som siste fallback hvis ingenting matcher.
_CATEGORY_FALLBACK = {
    "Frukt og grønt": "frukt-grønt-annet",
    "Meieri, ost og egg": "meieri-annet",
    "Pålegg": "pålegg-annet",
    "Bakeri og konditori": "bakeri-annet",
    "Kylling og kjøtt": "kjøtt-annet",
    "Fisk og sjømat": "fisk-annet",
    "Drikke": "drikke-annet",
    "Bakeingredienser": "bakeing-annet",
    "Frokostblandinger og müsli": "frokostblanding",
    "Hus og hjem": "hus-annet",
    "Hygiene og skjønnhet": "hygiene-annet",
    "Baby og barn": "baby-annet",
    "Middager og tilbehør": "middag-annet",
    "Grill": "grill-annet",
    "Sjokolade, snacks og godteri": "snacks",
    "Iskrem, dessert og kjeks": "iskrem-dessert",
    "Plantebasert": "plantebasert",
    "Faste, gode deals": "annet",
    "Mathall": "annet",
    "Blomster og planter": "blomster",
}


def product_type(name: str | None, category: str | None = None,
                 product_id: int | None = None) -> str | None:
    """Returnerer varetype for et produkt.

    Søker først i den eksplisitte mappingen, så via keyword-regler over
    produktnavnet, og til slutt via kategori-fallback. Hvis produktnavnet
    inneholder en størrelses-kode (Str. 5, Fra 6 mnd, Trinn 3, …) legges
    den til som suffiks så ulike størrelser av samme varetype håndteres
    som separate behov. Returnerer None hvis ingenting treffer (sjeldent —
    kategori-fallbacken dekker det meste)."""
    base: str | None = None

    if product_id is not None:
        # Valgt representant (jf. representatives.choose) pinner produktet
        # til varetypen det ble valgt for. Nøkkelen er komplett (inkl.
        # ev. størrelses-suffiks), så returner direkte.
        from .representatives import pinned_types

        pinned = pinned_types().get(int(product_id))
        if pinned:
            return pinned
        base = _explicit_mapping().get(int(product_id))

    if base is None and name:
        low = name.lower()
        for pattern, t in _KEYWORD_RULES:
            if pattern.search(low):
                base = t
                break

    if base is None and category and category in _CATEGORY_FALLBACK:
        base = _CATEGORY_FALLBACK[category]

    if base is None:
        return None

    if name:
        suffix = _extract_size_suffix(name)
        if suffix:
            return f"{base}-{suffix}"
    return base


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Legger til kolonnen `varetype` på en tabell med produkt-linjer.

    Dette er det eneste stedet varetype utledes for en tabell. Kallet er
    idempotent: finnes kolonnen allerede, returneres tabellen urørt, så
    hvert ledd i analysekjeden kan kalle den uten å betale for jobben på
    nytt. Klassifiseringen gjøres én gang per unike (product_id,
    product_name, category) og mappes tilbake på radene.
    """
    if "varetype" in df.columns:
        return df
    out = df.copy()
    if out.empty:
        out["varetype"] = pd.Series(dtype="object")
        return out
    if "category" in out.columns:
        kategorier = out["category"]
    else:
        kategorier = pd.Series([None] * len(out), index=out.index)
    sett: dict[tuple, str | None] = {}
    typer = []
    for pid, navn, kat in zip(out["product_id"], out["product_name"], kategorier):
        nokkel = (pid, navn, kat)
        if nokkel not in sett:
            sett[nokkel] = product_type(
                navn if pd.notna(navn) else None,
                kat if pd.notna(kat) else None,
                int(pid) if pd.notna(pid) else None,
            )
        typer.append(sett[nokkel])
    out["varetype"] = typer
    return out
