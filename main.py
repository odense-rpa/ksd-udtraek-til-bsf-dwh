import argparse
import asyncio
import logging
import os
from datetime import datetime
import sys
import unicodedata
import xml.etree.ElementTree as ET
from typing import Dict

from automation_server_client import (
    AutomationServer,
    Workqueue,
    Credential,
    WorkItemStatus,
)
from ksd.ksd import KSD
from odk_tools.reporting import report
from odk_tools.tracking import Tracker
from process.config import get_excel_mapping, load_excel_mapping

ksd_client: KSD
tracker: Tracker
proces_navn = "KSD Udtræk til BSF DWH"

EXPECTED_REPORT_FIELDS = [
    "Cpr",
    "AfsendtDato",
    "DinFoersteFravaersdag",
    "HvadFejlerDu",
    "UddybHvadDuFejler",
    "HarDuTidligereVaeretSygemeldtAfSammeAarsag",
    "ErDinSygedomLivstruende",
    "SkyldesDitFravaerEnArbejdsskade",
    "DinStilling",
    "DinUddannelse",
    "HvornaarForventerDuAtRaskmeldeDig_300A_300C",
    "OplysDatoForSidsteSygefravaersdag",
    "ForventerDuAtVendeTilbageTilDinArbejdsplads",
    "ErDuIBehandlingForDinSygdom",
    "JaHosEgenLaege",
    "NavnOgAdressePaaDinEgenLaege",
    "AfventerDuEnOperation",
    "HvorLaengeHarSygdommenPaavirketDitArbejdeFastholdelse",
    "HvilkeArbejdsopgaverErPaavirketAfSygdommen",
    "HvordanHarDinArbejdsgiverTilpassetArbejdetTilDinHelbredstilstand",
    "HarDuOgArbejdsgiverUdarbejdetPlanForTilbagevenden",
    "HarDuFaetLaegeerklaeringTilArbejdsgiver",
    "SamtykkeTilAtKommunenKanIndhenteHelbredsoplysninger",
    "SamtykkeTilAtKommunenKanIndhenteUdbetalingDanmarkOplysninger",
    "Telefonnummer",
]


def _normalize_name(value: str) -> str:
    """Normalize strings so XML tags and report field names can be matched."""
    folded = unicodedata.normalize("NFKD", value)
    ascii_like = "".join(char for char in folded if not unicodedata.combining(char))
    return "".join(char.lower() for char in ascii_like if char.isalnum())


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _extract_xml_values(xml_content: str) -> Dict[str, str]:
    root = ET.fromstring(xml_content)
    values: Dict[str, str] = {}

    for elem in root.iter():
        tag_name = _local_name(elem.tag)
        text = (elem.text or "").strip()
        if text:
            values.setdefault(_normalize_name(tag_name), text)

        for attr_name, attr_value in elem.attrib.items():
            cleaned_value = attr_value.strip()
            if cleaned_value:
                values.setdefault(_normalize_name(attr_name), cleaned_value)

    return values


def _format_date_ddmmyyyy(value: str) -> str:
    """Convert common date/datetime formats to dd-mm-yyyy."""
    raw = value.strip()
    if not raw:
        return ""

    # Support ISO timestamps with UTC marker.
    candidate = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate).strftime("%d-%m-%Y")
    except ValueError:
        pass

    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue

    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return f"{raw[8:10]}-{raw[5:7]}-{raw[0:4]}"

    return ""


def _build_report_payload(data: dict, xml_content: str) -> Dict[str, str | None]:
    xml_values = _extract_xml_values(xml_content)
    payload: Dict[str, str | None] = {field: "" for field in EXPECTED_REPORT_FIELDS}

    # Queue data is authoritative for CPR if present.
    payload["Cpr"] = data.get("Cpr")

    for field in EXPECTED_REPORT_FIELDS:
        normalized_field = _normalize_name(field)
        if normalized_field in xml_values:
            payload[field] = xml_values[normalized_field]

    payload["AfsendtDato"] = _format_date_ddmmyyyy(payload.get("AfsendtDato") or "")

    return payload


async def populate_queue(workqueue: Workqueue):
    data = get_excel_mapping()

    for row in data:
        workqueue.add_item(data=row, reference=f"{row['Cpr']}")


def process_workqueue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    with ksd_client:        
        ksd_client.login()

        for item in workqueue:
            with item:
                data = item.data
                path = None

                try:
                    path = ksd_client.hent_dokument_fra_sag(
                        data["Cpr"],
                        filtre={
                            "Første fraværsdag": data["Dato_FoersteFravaer"],
                            "Sagstype": "Sygefraværssag",
                        },
                        dokument_navn="Svar på Oplysningsskema",
                    )

                    with open(path, "r", encoding="utf-8") as f:
                        xml = f.read()

                    report_payload = _build_report_payload(data, xml)

                    report(
                        report_id="ksd_udtraek_til_bsf_dwh",
                        group="Data",
                        json=report_payload,
                    )

                    tracker.track_task(proces_navn)
                except RuntimeError as e:
                    report(
                        report_id="ksd_udtraek_til_bsf_dwh",
                        group="Fejl",
                        json={
                            "Cpr": data.get("Cpr", "Uden Cpr"),
                            "Fejl": "Kunne ikke finde sag eller dokument på borger",
                        },
                    )
                    logger.error(f"Error processing item: {data}. Error: {e}")
                    item.fail(str(e))
                except ET.ParseError as e:
                    report(
                        report_id="ksd_udtraek_til_bsf_dwh",
                        group="Fejl",
                        json={
                            "Cpr": data.get("Cpr", "Uden Cpr"),
                            "Fejl": "Kunne ikke parse XML fra dokument",
                        },
                    )
                    logger.error(f"XML parse error for item: {data}. Error: {e}")
                    item.fail(str(e))
                except Exception as e:
                    report(
                        report_id="ksd_udtraek_til_bsf_dwh",
                        group="Fejl",
                        json={
                            "Cpr": data.get("Cpr", "Uden Cpr"),
                            "Fejl": f"Uventet fejl: {str(e)}",
                        },
                    )                    
                    item.fail(str(e))
                    # Force restart the worker
                    raise
                finally:
                    ksd_client._luk_tabs()
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError as e:
                            logger.warning(
                                f"Could not remove downloaded file '{path}': {e}"
                            )


if __name__ == "__main__":
    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    ksd_credential = Credential.get_credential("RoboA")
    tracking_credential = Credential.get_credential("Odense SQL Server")

    if not ksd_credential.username or not ksd_credential.password:
        raise ValueError("RoboA credential mangler username eller password")

    if not tracking_credential.username or not tracking_credential.password:
        raise ValueError("Odense SQL Server credential mangler username eller password")

    ksd_client = KSD(
        brugernavn=f"{ksd_credential.username}@odense.dk",
        password=ksd_credential.password,
        idp=ksd_credential.data["idp"],
    )

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    parser = argparse.ArgumentParser(description=proces_navn)
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Populate the queue with test data and exit",
    )
    parser.add_argument(
        "--excel-file",
        default="./input/data.xlsx",
        help="Path to the Excel file containing mapping data (default: ./input/data.xlsx)",
    )

    args = parser.parse_args()

    load_excel_mapping(args.excel_file)

    # Queue management
    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        asyncio.run(populate_queue(workqueue))
        exit(0)

    # Process workqueue
    process_workqueue(workqueue)
