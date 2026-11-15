"""Tests for matching CMS hospitals to NYS DOH facility records."""

from __future__ import annotations

import pandas as pd
import pytest

from beyond_the_wait.data.cms_loader import load_cms_hospital_info
from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.facility_matching import (
    bronx_nys_hospitals,
    candidate_matches,
    classify_address_match,
    load_crosswalk,
    normalize_address,
    related_sites,
    select_bronx_cms_hospitals,
    street_number,
    validate_crosswalk,
)
from beyond_the_wait.data.nys_loader import load_nys_facilities
from tests.paths import CROSSWALK_PATH, PUBLIC_FIXTURE_RAW

EXPECTED_MATCHES = {
    "330009": ("001164", "exact_address"),  # BronxCare (Fulton Ave)
    "330059": ("001169", "exact_address"),  # Montefiore (Moses)
    "330080": ("001172", "exact_address"),  # Lincoln
    "330127": ("001165", "partial_address"),  # Jacobi ("PELHAM PKWY" vs "PELHAM PKWY S")
    "330399": ("001176", "exact_address"),  # St Barnabas / SBH Health System
}


@pytest.fixture(scope="module")
def cms_bronx() -> pd.DataFrame:
    hgi = load_cms_hospital_info(PUBLIC_FIXTURE_RAW / "cms_hospital_general_information.csv")
    return select_bronx_cms_hospitals(hgi)


@pytest.fixture(scope="module")
def registry() -> pd.DataFrame:
    return load_nys_facilities(PUBLIC_FIXTURE_RAW / "nys_health_facility_general_information.csv")


@pytest.fixture
def crosswalk() -> pd.DataFrame:
    return load_crosswalk(CROSSWALK_PATH)


# --- Address normalization ---------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("234 East 149th Street", "234 E 149TH ST"),
        ("234 EAST 149TH STREET", "234 E 149TH ST"),
        ("4422 Third Avenue", "4422 3RD AVE"),
        ("1400 PELHAM PARKWAY SOUTH", "1400 PELHAM PKWY S"),
        ("2475 St. Raymond Avenue", "2475 ST RAYMOND AVE"),
        ("3424 Kossuth Avenue & 210th Street", "3424 KOSSUTH AVE AND 210TH ST"),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


def test_street_number() -> None:
    assert street_number("1740-70 EASTCHESTER RD") == "1740-70"
    assert street_number("ONE TEST PLAZA") == ""
    assert street_number("") == ""


@pytest.mark.parametrize(
    ("cms", "nys", "expected"),
    [
        (("234 EAST 149TH STREET", "10451"), ("234 East 149th Street", "10451"), "exact_address"),
        (
            ("1400 PELHAM PARKWAY SOUTH", "10461"),
            ("1400 Pelham Parkway", "10461"),
            "partial_address",
        ),
        (("234 EAST 149TH STREET", "10451"), ("234 East 149th Street", "10452"), "no_match"),
        (("234 EAST 149TH STREET", "10451"), ("236 East 149th Street", "10451"), "no_match"),
        (("1 MAIN ST", "10451"), ("1 OTHER ST", "10451"), "no_match"),
        (("MAIN CAMPUS", "10451"), ("MAIN CAMPUS WEST", "10451"), "no_match"),
    ],
)
def test_classify_address_match(cms: tuple, nys: tuple, expected: str) -> None:
    assert classify_address_match(cms[0], cms[1], nys[0], nys[1]) == expected


# --- Selecting Bronx hospitals from CMS ------------------------------------------------


def test_bronx_selection_uses_official_location_and_ed_status(cms_bronx: pd.DataFrame) -> None:
    included = set(cms_bronx[cms_bronx["included"]]["Facility ID"])
    assert included == set(EXPECTED_MATCHES)
    assert "010001" not in set(cms_bronx["Facility ID"])  # not in the Bronx


def test_excluded_bronx_facilities_have_reasons(cms_bronx: pd.DataFrame) -> None:
    excluded = cms_bronx[~cms_bronx["included"]].set_index("Facility ID")
    assert set(excluded.index) == {"33016F", "334053"}
    assert "Veterans Administration" in excluded.loc["33016F", "exclusion_reason"]
    assert "Psychiatric" in excluded.loc["334053", "exclusion_reason"]


def test_no_emergency_services_is_excluded() -> None:
    hgi = pd.DataFrame(
        [
            {
                "Facility ID": "999001",
                "Facility Name": "FICTIONAL TEST HOSPITAL",
                "Address": "1 TEST WAY",
                "ZIP Code": "10451",
                "State": "NY",
                "County/Parish": "BRONX",
                "Hospital Type": "Acute Care Hospitals",
                "Emergency Services": "No",
            }
        ]
    )
    selected = select_bronx_cms_hospitals(hgi)
    assert not selected.iloc[0]["included"]
    assert "no emergency services" in selected.iloc[0]["exclusion_reason"]


# --- Automated candidates and the reviewed crosswalk ------------------------------------


def test_automated_candidates_match_expected(
    cms_bronx: pd.DataFrame, registry: pd.DataFrame
) -> None:
    found = candidate_matches(cms_bronx, bronx_nys_hospitals(registry))
    result = {r.cms_ccn: (r.nys_pfi, r.match_method) for r in found.itertuples()}
    assert result == EXPECTED_MATCHES


def test_ambiguous_candidates_are_flagged() -> None:
    cms = pd.DataFrame(
        [{"Facility ID": "999001", "Address": "1 TEST WAY", "ZIP Code": "10451", "included": True}]
    )
    nys = pd.DataFrame(
        [
            {"pfi": "999101", "Facility Address 1": "1 Test Way", "Facility Zip Code": "10451"},
            {"pfi": "999102", "Facility Address 1": "1 Test Way", "Facility Zip Code": "10451"},
        ]
    )
    row = candidate_matches(cms, nys).iloc[0]
    assert row["match_method"] == "ambiguous"
    assert row["nys_pfi"] == "999101;999102"


def test_single_exact_match_beats_partial() -> None:
    cms = pd.DataFrame(
        [{"Facility ID": "999001", "Address": "1 TEST WAY", "ZIP Code": "10451", "included": True}]
    )
    nys = pd.DataFrame(
        [
            {"pfi": "999101", "Facility Address 1": "1 Test Way", "Facility Zip Code": "10451"},
            {"pfi": "999102", "Facility Address 1": "1 Test", "Facility Zip Code": "10451"},
        ]
    )
    assert candidate_matches(cms, nys).iloc[0]["nys_pfi"] == "999101"


def test_no_candidate_gives_no_match() -> None:
    cms = pd.DataFrame(
        [{"Facility ID": "999001", "Address": "1 TEST WAY", "ZIP Code": "10451", "included": True}]
    )
    nys = pd.DataFrame(
        [{"pfi": "999101", "Facility Address 1": "9 Elsewhere", "Facility Zip Code": "10451"}]
    )
    assert candidate_matches(cms, nys).iloc[0]["match_method"] == "no_match"


def test_committed_crosswalk_validates_against_official_records(
    crosswalk: pd.DataFrame, cms_bronx: pd.DataFrame, registry: pd.DataFrame
) -> None:
    validate_crosswalk(crosswalk, cms_bronx, registry)
    result = {r.cms_ccn: (r.nys_pfi, r.match_method) for r in crosswalk.itertuples()}
    assert result == EXPECTED_MATCHES


def test_crosswalk_names_come_from_official_records(
    crosswalk: pd.DataFrame, cms_bronx: pd.DataFrame, registry: pd.DataFrame
) -> None:
    cms_names = dict(zip(cms_bronx["Facility ID"], cms_bronx["Facility Name"], strict=True))
    nys_names = dict(zip(registry["pfi"], registry["Facility Name"], strict=True))
    for row in crosswalk.itertuples():
        assert row.cms_facility_name == cms_names[row.cms_ccn]
        assert row.nys_facility_name == nys_names[row.nys_pfi]
    assert {"LINCOLN MEDICAL & MENTAL HEALTH CENTER", "ST BARNABAS HOSPITAL"} <= set(
        crosswalk["cms_facility_name"]
    )
    assert "BronxCare Hospital Center" in set(crosswalk["nys_facility_name"])


def _break(crosswalk: pd.DataFrame, ccn: str, **changes: str) -> pd.DataFrame:
    broken = crosswalk.copy()
    for col, value in changes.items():
        broken.loc[broken["cms_ccn"] == ccn, col] = value
    return broken


@pytest.mark.parametrize(
    ("ccn", "changes", "message"),
    [
        # Wrong NYS site (North Central Bronx instead of Jacobi).
        ("330127", {"nys_pfi": "001186", "nys_facility_name": "North Central Bronx Hospital"},
         "addresses now give 'no_match'"),
        ("330080", {"cms_facility_name": "LINCOLN HOSPITAL"}, "crosswalk name"),
        ("330080", {"nys_facility_name": "Lincoln"}, "crosswalk NYS name"),
        ("330080", {"match_method": "ambiguous"}, "is not accepted"),
        ("330080", {"nys_pfi": "999999"}, "not found in registry"),
        ("330080", {"nys_pfi": "010006"}, "is in Suffolk, not Bronx"),
        ("330080", {"cms_ccn": "33016F"}, "not an included CMS Bronx ED hospital"),
    ],
)  # fmt: skip
def test_crosswalk_problems_are_reported(
    crosswalk: pd.DataFrame,
    cms_bronx: pd.DataFrame,
    registry: pd.DataFrame,
    ccn: str,
    changes: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(PublicDataError, match=message):
        validate_crosswalk(_break(crosswalk, ccn, **changes), cms_bronx, registry)


def test_crosswalk_missing_hospital_and_duplicates(
    crosswalk: pd.DataFrame, cms_bronx: pd.DataFrame, registry: pd.DataFrame
) -> None:
    with pytest.raises(PublicDataError, match="missing from crosswalk: \\['330399'\\]"):
        validate_crosswalk(crosswalk[crosswalk["cms_ccn"] != "330399"], cms_bronx, registry)
    doubled = pd.concat([crosswalk, crosswalk.head(1)])
    with pytest.raises(PublicDataError, match="duplicate cms_ccn"):
        validate_crosswalk(doubled, cms_bronx, registry)


def test_related_sites_from_official_main_site_field(registry: pd.DataFrame) -> None:
    assert related_sites("001165", registry) == [("001186", "North Central Bronx Hospital")]
    assert related_sites("001172", registry) == []
