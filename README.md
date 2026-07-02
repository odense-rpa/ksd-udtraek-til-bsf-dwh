# KSD-udtræk til BSF Data Warehouse

Robotten udtrækker strukturerede helbredsoplysninger fra sygefravaerssager i KSD og rapporterer dem til BSF Data Warehouse.

## Hvad gør robotten?

1. Indlæser Excel-fil med mapping-data (CPR-numre og første fraværsdage) fra `./input/data.xlsx` og populerer arbejdskøen.
2. Logger ind i KSD med RoboA-credentials (kræver `username`, `password` og `idp`-felt).
3. For hvert element i køen: finder sygefravaerssagen i KSD baseret på CPR-nummer og første fraværsdag.
4. Henter dokumentet "Svar på Oplysningsskema" fra sagen som XML-fil.
5. Parser XML-dokumentet og mapper felterne til det forventede rapportformat med datanormalisering.
6. Rapporterer de udtrukne data til BSF DWH via `odk_tools` (report_id: `ksd_udtraek_til_bsf_dwh`).
7. Ved fejl (sag ikke fundet, dokument ikke fundet, XML-parse-fejl eller uventet fejl) rapporteres en fejlpost med CPR-nummer og fejlbeskrivelse.
8. Tracker succesfulde behandlinger via Tracker (Odense SQL Server).

## Forudsætninger

- Python ≥ 3.13
- [`uv`](https://docs.astral.sh/uv/) til pakkehåndtering
- Adgang til **Automation Server** (arbejdskø)
- Adgang til **KSD** (Kommunernes Sygedagpengesystem)
- Adgang til **BSF Data Warehouse**
- Adgang til **Odense SQL Server** (tracking)

## Installation

```sh
uv sync
```

## Konfiguration

Credentials registreres i Automation Server:

- `RoboA` — brugernavn, adgangskode og `idp`-felt til KSD-login
- `Odense SQL Server` — forbindelsesoplysninger til tracking-databasen

Inputfilen (`./input/data.xlsx`) er et Excel-ark med CPR-numre og første fraværsdage, leveret af kolleger. En alternativ sti kan angives med `--excel-file`.

| Miljøvariabel | Beskrivelse |
|---|---|
| `ATS_URL` | URL til Automation Server API |
| `ATS_TOKEN` | Adgangstoken til Automation Server |
| `ATS_WORKQUEUE_OVERRIDE` | Overstyr arbejdskø-ID (valgfri) |

## Kørsel

```sh
uv run python main.py --queue                         # Fyld arbejdskøen fra Excel-fil
uv run python main.py                                 # Behandl arbejdskøen
uv run python main.py --excel-file sti/til/fil.xlsx   # Brug alternativ Excel-fil
```

## Afhængigheder

| Pakke | Formål |
|---|---|
| `automation-server-client` | Klient til Automation Server — arbejdskø, credentials og workitems |
| `ksd` | Klient til KSD til login og hentning af dokumenter fra sygefravaerssager |
| `odk-tools` | Rapportering til BSF DWH og tracking af behandlede opgaver |
| `openpyxl` | Læsning af Excel-inputfil med CPR-numre og fraværsdatoer |

## GDPR og sikkerhed

Processen behandler følgende personoplysninger fra KSD:

- CPR-numre og første fraværsdag
- Helbredsoplysninger (sygdomsbeskrivelse, behandling, operation, livstruende sygdom)
- Arbejdsskadeoplysninger
- Stilling og uddannelse
- Arbejdsgiveroplysninger
- Telefonnummer
- Samtykke til indhentning af helbreds- og udbetalingsoplysninger

Data overføres til BSF Data Warehouse og tilgås kun af medarbejdere med adgang til BSF DWH-rapporter. Oplysningerne behandles i henhold til den databehandleraftale, der er indgået med leverandørerne.
