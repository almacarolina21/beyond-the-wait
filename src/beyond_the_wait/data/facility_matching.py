"""Match CMS hospitals to NYS DOH facility records -- the Bronx facility crosswalk.

The problem
    CMS identifies a hospital by its CMS Certification Number (CCN, e.g.
    "330080"). NYS DOH identifies each physical site by a PFI (e.g. "001172").
    Neither public file contains the other's ID, so there is no official key
    to join on.

Our approach (two independent automated checks + a curated crosswalk)
    1. Location filter: a CMS hospital is a Bronx candidate only if CMS itself
       lists County = BRONX, State = NY, an acute-care hospital type, and
       emergency services = Yes.
    2. Automated candidate matching: compare normalized street address + ZIP
       against the NYS registry. "exact_address" means identical after
       normalization; "partial_address" means same ZIP and street number with
       one street name a prefix of the other (e.g. "PELHAM PKWY" vs
       "PELHAM PKWY S"). Anything else is "no_match".
    3. Curated crosswalk: accepted matches live in a committed crosswalk file
       (data/public/reference/bronx_facility_crosswalk.csv), recorded during
       AI-assisted development and not yet independently verified by a
       person. The pipeline
       re-checks that file against the current official records every build.
       If an agency renames or moves a facility, the build fails and asks for
       review instead of silently re-matching.

What a match does NOT mean
    CMS reports results per CCN. A CCN can cover more than one physical site,
    and the NYS registry shows several Bronx sites that list another site as
    their "main site". From these public files we can't determine which
    sites contribute to a CMS figure, so we record the related sites and say
    so, rather than assume.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from beyond_the_wait.data._csv import read_text_csv
from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.nys_loader import normalize_pfi

CROSSWALK_COLUMNS: tuple[str, ...] = (
    "cms_ccn",
    "cms_facility_name",
    "nys_pfi",
    "nys_facility_name",
    "match_method",
    "reviewed_on",
    "review_note",
)
ACCEPTED_METHODS = ("exact_address", "partial_address")

_TOKEN_MAP = {
    "AVENUE": "AVE", "AV": "AVE", "STREET": "ST", "PARKWAY": "PKWY", "ROAD": "RD",
    "PLACE": "PL", "BOULEVARD": "BLVD", "DRIVE": "DR", "EAST": "E", "WEST": "W",
    "NORTH": "N", "SOUTH": "S", "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD",
}  # fmt: skip


def normalize_address(raw: str) -> str:
    """'234 East 149th Street' -> '234 E 149TH ST' (uppercase, standard abbreviations)."""
    text = raw.upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9\- ]", " ", text)
    return " ".join(_TOKEN_MAP.get(token, token) for token in text.split())


def street_number(normalized: str) -> str:
    """Leading house number ('1740-70 EASTCHESTER RD' -> '1740-70'), or ''."""
    first = normalized.split(" ", 1)[0] if normalized else ""
    return first if re.fullmatch(r"\d+(-\d+)?", first) else ""


def zip5(raw: str) -> str:
    return raw.strip()[:5]


def classify_address_match(cms_address: str, cms_zip: str, nys_address: str, nys_zip: str) -> str:
    """Return 'exact_address', 'partial_address', or 'no_match'."""
    if zip5(cms_zip) != zip5(nys_zip):
        return "no_match"
    a, b = normalize_address(cms_address), normalize_address(nys_address)
    if a == b:
        return "exact_address"
    if street_number(a) and street_number(a) == street_number(b):
        ta, tb = a.split(), b.split()
        shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if longer[: len(shorter)] == shorter:
            return "partial_address"
    return "no_match"


def select_bronx_cms_hospitals(hospital_info: pd.DataFrame) -> pd.DataFrame:
    """Bronx facilities in the CMS directory, with an inclusion decision and reason."""
    bronx = hospital_info[
        (hospital_info["State"] == "NY") & (hospital_info["County/Parish"].str.upper() == "BRONX")
    ].copy()

    def reason(row: pd.Series) -> str:
        if row["Hospital Type"] != "Acute Care Hospitals":
            return (
                f"Hospital type is '{row['Hospital Type']}'; only acute care hospitals appear "
                "in the CMS Timely and Effective Care hospital file used here."
            )
        if row["Emergency Services"] != "Yes":
            return "CMS lists no emergency services."
        return ""

    bronx["exclusion_reason"] = bronx.apply(reason, axis=1)
    bronx["included"] = bronx["exclusion_reason"] == ""
    return bronx.sort_values("Facility ID").reset_index(drop=True)


def bronx_nys_hospitals(registry: pd.DataFrame) -> pd.DataFrame:
    """NYS registry rows for Bronx general hospitals (short description 'HOSP')."""
    mask = (registry["Facility County"] == "Bronx") & (registry["Short Description"] == "HOSP")
    return registry[mask].sort_values("pfi").reset_index(drop=True)


def candidate_matches(cms_bronx: pd.DataFrame, nys_hospitals: pd.DataFrame) -> pd.DataFrame:
    """Automated address matches for every included CMS Bronx hospital."""
    rows = []
    for _, c in cms_bronx[cms_bronx["included"]].iterrows():
        found = []
        for _, n in nys_hospitals.iterrows():
            method = classify_address_match(
                c["Address"], c["ZIP Code"], n["Facility Address 1"], n["Facility Zip Code"]
            )
            if method != "no_match":
                found.append((n["pfi"], method))
        if len(found) > 1:
            # One exact match beats partial ones; otherwise a human must decide.
            exact = [f for f in found if f[1] == "exact_address"]
            if len(exact) == 1:
                found = exact
            else:
                found = [(";".join(sorted(p for p, _ in found)), "ambiguous")]
        pfi, method = found[0] if found else ("", "no_match")
        rows.append({"cms_ccn": c["Facility ID"], "nys_pfi": pfi, "match_method": method})
    return pd.DataFrame(rows, columns=["cms_ccn", "nys_pfi", "match_method"])


def load_crosswalk(path: str | Path) -> pd.DataFrame:
    """Load the curated crosswalk, normalizing PFIs."""
    df = read_text_csv(path, CROSSWALK_COLUMNS, "Bronx facility crosswalk")
    df["nys_pfi"] = df["nys_pfi"].map(normalize_pfi)
    return df


def validate_crosswalk(
    crosswalk: pd.DataFrame, cms_bronx: pd.DataFrame, nys_registry: pd.DataFrame
) -> None:
    """Check the curated crosswalk against current official records.

    Raises PublicDataError listing every problem found, so a reviewer sees
    them all at once.
    """
    problems: list[str] = []
    included = cms_bronx[cms_bronx["included"]].set_index("Facility ID")
    registry = nys_registry.set_index("pfi")
    auto = candidate_matches(cms_bronx, bronx_nys_hospitals(nys_registry)).set_index("cms_ccn")

    for col in ("cms_ccn", "nys_pfi"):
        dupes = crosswalk[crosswalk[col].duplicated()][col].tolist()
        if dupes:
            problems.append(f"duplicate {col} in crosswalk: {dupes}")

    missing = sorted(set(included.index) - set(crosswalk["cms_ccn"]))
    if missing:
        problems.append(f"CMS Bronx ED hospitals missing from crosswalk: {missing}")

    for _, row in crosswalk.iterrows():
        ccn, pfi = row["cms_ccn"], row["nys_pfi"]
        if ccn not in included.index:
            problems.append(f"{ccn}: not an included CMS Bronx ED hospital")
            continue
        if pfi not in registry.index:
            problems.append(f"{ccn}: NYS PFI {pfi} not found in registry")
            continue
        cms, nys = included.loc[ccn], registry.loc[pfi]
        if nys["Facility County"] != "Bronx":
            problems.append(f"{ccn}: NYS PFI {pfi} is in {nys['Facility County']}, not Bronx")
        if row["cms_facility_name"] != cms["Facility Name"]:
            problems.append(
                f"{ccn}: crosswalk name {row['cms_facility_name']!r} != "
                f"CMS {cms['Facility Name']!r}"
            )
        if row["nys_facility_name"] != nys["Facility Name"]:
            problems.append(
                f"{ccn}: crosswalk NYS name {row['nys_facility_name']!r} != "
                f"registry {nys['Facility Name']!r}"
            )
        if row["match_method"] not in ACCEPTED_METHODS:
            problems.append(f"{ccn}: match_method {row['match_method']!r} is not accepted")
        method_now = classify_address_match(
            cms["Address"], cms["ZIP Code"], nys["Facility Address 1"], nys["Facility Zip Code"]
        )
        if method_now != row["match_method"]:
            problems.append(
                f"{ccn}: reviewed as {row['match_method']!r} but addresses now give {method_now!r}"
            )
        if ccn in auto.index and auto.loc[ccn, "nys_pfi"] != pfi:
            problems.append(
                f"{ccn}: automated matching now suggests {auto.loc[ccn, 'nys_pfi']!r}, "
                f"crosswalk says {pfi!r}"
            )

    if problems:
        raise PublicDataError(
            "Bronx facility crosswalk needs review:\n" + "\n".join(f"  - {p}" for p in problems)
        )


def related_sites(pfi: str, nys_registry: pd.DataFrame) -> list[tuple[str, str]]:
    """NYS sites whose official 'Main Site Facility ID' is this PFI."""
    rel = nys_registry[nys_registry["main_site_pfi"] == pfi].sort_values("pfi")
    return list(zip(rel["pfi"], rel["Facility Name"], strict=True))
