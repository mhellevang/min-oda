# Min Oda

Enbruker-app som leser ordrehistorikk fra oda.com og foreslår hva som bør handles, basert på kjøpsrytme per behov (ikke per produkt).

## Language

**Varetype**:
Et behov som flere substituerbare produkter kan dekke ("bleier-str6", "melk"). Kadens og handleliste regnes per varetype, ikke per produkt.
_Avoid_: kategori (det er Odas grovinndeling), produktgruppe

**Representant**:
Produktet som vises i handlelista for en varetype. Uten et eksplisitt valg er det den mest kjøpte varianten.

**Valgt representant**:
Et produkt brukeren eksplisitt har pekt ut som representant for en varetype, typisk en katalogvare som aldri er kjøpt. Overstyrer mest-kjøpt-logikken til valget fjernes, og pinner samtidig produktets varetype-klassifisering.
_Avoid_: favoritt, override

**Katalogvare**:
Et produkt fra Odas søke-API som ikke finnes i egen kjøpshistorikk. Har ingen kadens og kan bare vises i lista som valgt representant.

**Engangsvare**:
En katalogvare som legges rett i Oda-kurven uten å påvirke varetyper, representanter eller kadens.
_Avoid_: impulskjøp

**Kadens**:
Median antall dager mellom kjøp innen en varetype. Grunnlaget for forfalt/snart/i rute-status og foreslått antall.

**Blokkering**:
Å skjule et produkt (produktnivå) eller en hel varetype (varetypenivå) fra forslag. Produktblokkering lar en annen variant av samme varetype overta.
