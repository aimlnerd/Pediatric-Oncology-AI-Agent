"""Build the protocol manifest from the BSPHO mapping table.

The manifest is the single source for Vertex AI Search document metadata and for
Center participation (which stays out of the index, see ADR 0002).

Usage:
    uv run --with openpyxl python scripts/build_protocol_manifest.py
"""

import json
import re
from pathlib import Path
from typing import TypedDict

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parent.parent
XLSX = (
    ROOT
    / "swg-use-cases"
    / "1. Virtual Assistant"
    / "SwG-UC1-Golden Dataset and mapping table.xlsx"
)
MANIFEST = ROOT / "packages" / "agent" / "src" / "agent" / "data" / "protocol_manifest.json"
CENTERS = ("UZA", "UZL", "CHU", "UCL", "HUDERF", "UZG")

# Version and date as printed in each filename; None where the filename does not say.
# Review against the PDF title page before relying on a value marked None.
VERSIONS: dict[str, tuple[str | None, str | None]] = {
    "01_Protocol_ML_DS_2018_v3_0_clean.pdf": ("3.0", None),
    "02_a_SIOPE_ATRT01_Protocol_V1.5.pdf": ("1.5", None),
    "2013-002766-39_PROTOCOLE_Final_V4.0_20220711_SIOP Ependymoma II.pdf": ("4.0", "2022-07-11"),
    "2013-004039-60_PROTOCOLE_V4.0_20211001_Final_TGM2013-NS.PDF": ("4.0", "2021-10-01"),
    "ALCL-99-NHL_2000_06.pdf": (None, None),
    "ALLTogether1 Master Protocol v6.0 07Mar25_CLEAN3_signed.pdf": ("6.0", "2025-03-07"),
    "D1_CHIP-AML22_Master_protocol_v2-2_20OCT2023_FINAL_Signed.pdf": ("2.2", "2023-10-20"),
    "D1_Protocol 2024-516115-24.pdf": (None, None),
    "D1_Protocol HRNBL2_2019-001068-31_Clean.pdf": (None, None),
    "EE2012-Protocolv5.0_1402-STBSG-EE2012-_02Jun2017.pdf": ("5.0", "2017-06-02"),
    "EWOG-SAA-2010-Final_120424.pdf": ("Final", None),
    "EuroNet recommendations 6-2023.pdf": (None, "2023-06"),
    "EuroNet-PHL-C2_trial_protocol_final7.0_2020-09-05.pdf": ("7.0", "2020-09-05"),
    "FaR-RMS Protocol Version 2.0c 21-Mar-2024.pdf": ("2.0c", "2024-03-21"),
    "HLH2004-Protocol.pdf": (None, None),
    "IF-21_Protocol_v1.4_18Feb2025.pdf": ("1.4", "2025-02-18"),
    "Inter-B-NHLritux2010V414dec2015_clean.pdf": ("4", "2015-12-14"),
    "LCH-IV_Protocol_v1.7_01NOV2022.pdf": ("1.7", "2022-11-01"),
    "LINES_Protocol_version-8.0.pdf": ("8.0", None),
    "PHITT-Protocol_version-3-0-17Oct2018.pdf": ("3.0", "2018-10-17"),
    "Protocol EWOG-MDS 2006_version 30112006.pdf": (None, "2006-11-30"),
    "Protocol-EURAMOS1_v3-0_21-07-2011_FINAL_clean.pdf": ("3.0", "2011-07-21"),
    "SIOP HRMB Protocol v3.0 22Jun2023_clean.pdf": ("3.0", "2023-06-22"),
    "SIOP Umbrella_PartA_v3.2_May2022_clean.pdf": ("3.2", "2022-05"),
    "SIOP Umbrella_PartB_v3.2_May2022_clean.pdf": ("3.2", "2022-05"),
    "SIOP-LGG-2004-Master-Protokoll-SIOP-Version-I-April-2004.pdf": ("I", "2004-04"),
    "iEE_ISF_04_a_Protocol_Version_1.5.pdf": ("1.5", None),
}

# Short titles that make poor identifiers.
PROTOCOL_ID_OVERRIDES = {
    "ALLTogether1 Master Protocol": "alltogether1",
    "BEACON2 (Trial EU CT: 2024-516115-24)": "beacon2",
}


class ProtocolEntry(TypedDict):
    protocol_id: str
    short_title: str
    title: str
    filename: str
    version: str | None
    version_date: str | None
    doc_type: str
    centers: list[str]


def slugify(short_title: str) -> str:
    if short_title in PROTOCOL_ID_OVERRIDES:
        return PROTOCOL_ID_OVERRIDES[short_title]
    return re.sub(r"[^a-z0-9]+", "-", short_title.lower()).strip("-")


def read_protocols(xlsx: Path) -> list[ProtocolEntry]:
    sheet = load_workbook(xlsx, read_only=True, data_only=True)["Mapping Table_Hospitals-Protoco"]
    rows = sheet.iter_rows(values_only=True)
    header = [str(cell).strip() if cell else "" for cell in next(rows)]
    entries: list[ProtocolEntry] = []
    for row in rows:
        record = dict(zip(header, row, strict=False))
        filename = str(record.get("Original File Name") or "").strip()
        is_protocol = str(record.get("Protocol") or "").strip().lower() == "x"
        if not filename or not is_protocol:
            continue
        short_title = str(record["Short Title"]).strip()
        version, version_date = VERSIONS[filename]
        entries.append(
            ProtocolEntry(
                protocol_id=slugify(short_title),
                short_title=short_title,
                title=str(record["File Title"]).strip(),
                filename=filename,
                version=version,
                version_date=version_date,
                doc_type="protocol",
                centers=[c for c in CENTERS if str(record.get(c) or "").strip().lower() == "x"],
            )
        )
    return entries


def main() -> None:
    entries = read_protocols(XLSX)
    ids = [e["protocol_id"] for e in entries]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"Duplicate protocol_id in {ids}")
    missing = set(VERSIONS) - {e["filename"] for e in entries}
    if missing:
        raise SystemExit(f"Version table lists files not in the mapping table: {missing}")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} protocols to {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
