#!/usr/bin/env python3
"""Rebuild the reviewed DeskBuddy SMD v1.0 CAM repair archive.

This is intentionally a narrow, revision-locked Gerber repair.  It refuses to
run if the input archive or any expected source geometry differs from the
reviewed 2026-09-19 export.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import zipfile


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "Gerber_DESKBUDDY-ROBOT-ARM-v1-SMD_DeskBuddy_SMD_v1.0_2026-09-19.zip"
OUTPUT = HERE / "Gerber_DESKBUDDY_SMD_v1.0_CAM_REPAIRED_2026-09-19.zip"
SOURCE_SHA256 = "94e171042859ff446390d34d668160e35bff31cd89087438d259a2aed24ce4e3"
REMOVED = "Drill_PTH_Through_Via.DRL"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(data: bytes, old: str, new: str, label: str) -> bytes:
    before = old.encode("ascii")
    after = new.encode("ascii")
    count = data.count(before)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source match, found {count}")
    return data.replace(before, after)


def repair_outline(data: bytes) -> bytes:
    old = """D10*
X2095500Y17373600D02*
G01*
X2095500Y17369281D01*
X2095500Y17369281D01*
X2095500Y17369281D01*
X2095500Y9347200D01*
X6108700Y9347200D01*
X6108700Y15557500D01*
X5600700Y15557500D01*
X5600700Y17348200D01*
X2095500Y17348200D01*
X2095500Y17367504D01*
X2095500Y17360900D01*"""
    new = """D10*
X2095500Y9347200D02*
G01*
X6108700Y9347200D01*
X6108700Y15557500D01*
X5600700Y15557500D01*
X5600700Y17348200D01*
X2095500Y17348200D01*
X2095500Y9347200D01*"""
    return replace_once(data, old, new, "board outline")


def repair_bottom(data: bytes) -> bytes:
    replacements = [
        (
            """X2225166Y11760200D01*
X2137384Y11672417D01*
X2137384Y9991115D01*
X2273300Y9855200D01*""",
            """X2225166Y11760200D01*
X2150000Y11685034D01*
X2150000Y9978500D01*
X2273300Y9855200D01*""",
            "bottom left-edge route",
        ),
        (
            """X2391206Y14427200D02*
G01*
X2509291Y14309115D01*
X5223484Y14309115D01*
X5295900Y14236700D01*""",
            """X2391206Y14427200D02*
G01*
X2516406Y14302000D01*
X5230600Y14302000D01*
X5295900Y14236700D01*""",
            "bottom horizontal signal route",
        ),
        (
            """X5666689Y12821005D01*
X4868799Y12023115D01*
X4781981Y12023115D01*
X4695215Y11936349D01*
X4695215Y10481284D01*
X4813300Y10363200D01*""",
            """X5666689Y12821005D01*
X4864420Y12017900D01*
X4769500Y12017900D01*
X4688000Y11936349D01*
X4688000Y10488500D01*
X4813300Y10363200D01*""",
            "bottom inner right-header route",
        ),
    ]
    for old, new, label in replacements:
        data = replace_once(data, old, new, label)
    return data


def repair_top(data: bytes) -> bytes:
    replacements = [
        (
            """X4631537Y10121011D01*
X4770297Y9982250D01*""",
            """X4631537Y10115000D01*
X4764287Y9982250D01*""",
            "top left diagonal beside right-hand header",
        ),
        (
            """X5049748Y10042448D01*
X4855997Y10236200D01*""",
            """X5049748Y10050000D01*
X4863548Y10236200D01*""",
            "top right diagonal beside right-hand header",
        ),
        (
            """X4755489Y10236200D01*
X4692167Y10299522D01*
X4692167Y12495961D01*
X4813300Y12617094D01*""",
            """X4751322Y10236200D01*
X4688000Y10299522D01*
X4688000Y12491794D01*
X4813300Y12617094D01*""",
            "top inner right-header route",
        ),
        (
            """X4813300Y10617200D02*
G01*
X5025720Y10829620D01*
X5025720Y14370507D01*""",
            """X4813300Y10617200D02*
G01*
X5030000Y10833900D01*
X5030000Y14366227D01*
X5025720Y14370507D01*""",
            "top long signal route",
        ),
    ]
    for old, new, label in replacements:
        data = replace_once(data, old, new, label)
    return data


REPAIRS = {
    "Gerber_BoardOutlineLayer.GKO": repair_outline,
    "Gerber_BottomLayer.GBL": repair_bottom,
    "Gerber_TopLayer.GTL": repair_top,
}


def main() -> int:
    if not SOURCE.is_file():
        raise FileNotFoundError(SOURCE)
    source_bytes = SOURCE.read_bytes()
    actual_source_hash = sha256(source_bytes)
    if actual_source_hash != SOURCE_SHA256:
        raise RuntimeError(
            f"Refusing unknown source archive: {actual_source_hash} != {SOURCE_SHA256}"
        )

    with zipfile.ZipFile(SOURCE, "r") as source_zip:
        names = source_zip.namelist()
        if names.count(REMOVED) != 1:
            raise RuntimeError(f"Expected exactly one {REMOVED} entry")
        if not set(REPAIRS).issubset(names):
            raise RuntimeError("Source archive is missing a repair target")

        with zipfile.ZipFile(OUTPUT, "w") as output_zip:
            for info in source_zip.infolist():
                if info.filename == REMOVED:
                    continue
                data = source_zip.read(info.filename)
                repair = REPAIRS.get(info.filename)
                if repair:
                    revised = repair(data)
                    if revised == data:
                        raise RuntimeError(f"Repair did not change {info.filename}")
                    data = revised
                output_zip.writestr(info, data)

    with zipfile.ZipFile(OUTPUT, "r") as result_zip:
        if result_zip.testzip() is not None:
            raise RuntimeError("Output ZIP CRC validation failed")
        result_names = result_zip.namelist()
        if REMOVED in result_names or len(result_names) != len(names) - 1:
            raise RuntimeError("Output ZIP member set is incorrect")
        for name in result_names:
            if name not in REPAIRS:
                with zipfile.ZipFile(SOURCE, "r") as source_zip:
                    if result_zip.read(name) != source_zip.read(name):
                        raise RuntimeError(f"Unchanged member differs: {name}")

    print(OUTPUT)
    print(f"SHA-256: {sha256(OUTPUT.read_bytes())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
