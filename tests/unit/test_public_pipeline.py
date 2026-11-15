"""Tests for the end-to-end public-data pipeline (offline, using fixtures)."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.public_pipeline import (
    BUILD_MANIFEST_FILE,
    FACILITIES_FILE,
    MEASURES_FILE,
    build_bronx_reference,
    download_sources,
    load_processed_measures,
    register_local_file,
    resolve_download,
    verify_raw_files,
)
from beyond_the_wait.data.public_sources import (
    CMS_TIMELY_EFFECTIVE_CARE,
    NYS_ED_ENCOUNTERS_BY_FACILITY,
    SOURCES,
)
from tests.paths import CROSSWALK_PATH, PROCESSED_DIR, PUBLIC_FIXTURE_RAW


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def raw_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "raw"
    shutil.copytree(PUBLIC_FIXTURE_RAW, dest)
    return dest


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("processed")
    build_bronx_reference(PUBLIC_FIXTURE_RAW, CROSSWALK_PATH, out)
    return out


# --- Build ----------------------------------------------------------------------


def test_build_writes_outputs(built: Path) -> None:
    for name in (MEASURES_FILE, FACILITIES_FILE, BUILD_MANIFEST_FILE):
        assert (built / name).is_file()


def test_every_row_is_public_with_source_details(built: Path) -> None:
    measures = load_processed_measures(built / MEASURES_FILE)
    assert set(measures["source_dataset_id"]) == {"yv7e-xc69", "5gzv-zv2z"}
    assert (measures["retrieved_on"] == "2026-11-15").all()
    assert measures["source_url"].str.startswith("https://").all()


def test_missing_and_suppressed_values_stay_empty(built: Path) -> None:
    m = load_processed_measures(built / MEASURES_FILE)
    not_reported = m[m["status"] != "reported"]
    assert set(not_reported["status"]) == {"not_available", "suppressed"}
    assert not_reported["value"].isna().all()
    assert (not_reported["missing_reason"].str.len() > 0).all()


def test_ids_keep_leading_zeros_in_output(built: Path) -> None:
    m = load_processed_measures(built / MEASURES_FILE)
    assert "001164" in set(m["nys_pfi"])
    assert m.loc[m["facility_id_type"] == "nys_pfi", "facility_id"].str.fullmatch(r"\d{6}").all()


def test_cms_and_nys_rows_are_linked_through_the_crosswalk(built: Path) -> None:
    m = load_processed_measures(built / MEASURES_FILE)
    lincoln_nys = m[
        (m["source_key"] == NYS_ED_ENCOUNTERS_BY_FACILITY.key) & (m["nys_pfi"] == "001172")
    ]
    assert set(lincoln_nys["cms_ccn"]) == {"330080"}
    ncb = m[m["nys_pfi"] == "001186"]
    assert set(ncb["cms_ccn"].fillna("")) == {""}  # not matched to any CMS record


def test_no_door_to_provider_rows(built: Path) -> None:
    m = load_processed_measures(built / MEASURES_FILE)
    assert "door_to_provider" not in set(m["measure_kind"])


def test_reporting_period_consistent_within_each_cms_measure(built: Path) -> None:
    m = load_processed_measures(built / MEASURES_FILE)
    cms = m[m["source_key"] == CMS_TIMELY_EFFECTIVE_CARE.key]
    assert (cms.groupby("measure_id")[["period_start", "period_end"]].nunique() == 1).all().all()


def test_facilities_table_explains_every_facility(built: Path) -> None:
    f = pd.read_csv(built / FACILITIES_FILE, dtype=str, keep_default_na=False)
    m = load_processed_measures(built / MEASURES_FILE)
    assert set(m["facility_id"]) <= set(f["cms_ccn"]) | set(f["nys_pfi"])
    assert f.groupby("status").size().to_dict() == {"excluded": 2, "matched": 5, "nys_only": 8}


def test_build_is_byte_for_byte_reproducible(built: Path, tmp_path: Path) -> None:
    build_bronx_reference(PUBLIC_FIXTURE_RAW, CROSSWALK_PATH, tmp_path)
    for name in (MEASURES_FILE, FACILITIES_FILE, BUILD_MANIFEST_FILE):
        assert _sha(built / name) == _sha(tmp_path / name), name


def test_fixture_build_matches_committed_measures_file(built: Path) -> None:
    # The fixtures contain every record the Bronx build uses, so building from
    # them must reproduce the committed file built from the full official data.
    assert _sha(built / MEASURES_FILE) == _sha(PROCESSED_DIR / MEASURES_FILE)


def test_build_manifest_records_inputs_and_outputs(built: Path) -> None:
    manifest = json.loads((built / BUILD_MANIFEST_FILE).read_text())
    assert set(manifest["inputs"]) == set(SOURCES)
    assert manifest["outputs"][MEASURES_FILE]["sha256"] == _sha(built / MEASURES_FILE)
    assert manifest["parameters"] == {"county": "Bronx", "nys_years": [2024, 2025]}
    assert manifest["crosswalk_sha256"] == _sha(CROSSWALK_PATH)


# --- Raw-file integrity -----------------------------------------------------------------


def test_tampered_raw_file_is_rejected(raw_copy: Path, tmp_path: Path) -> None:
    target = raw_copy / "cms_timely_effective_care_hospital.csv"
    original = target.read_text()
    tampered = original.replace(",228,313,", ",128,313,")  # Lincoln OP_18b
    assert tampered != original  # guard: make sure the edit really happened
    target.write_text(tampered)
    with pytest.raises(PublicDataError, match="does not match its manifest fingerprint"):
        build_bronx_reference(raw_copy, CROSSWALK_PATH, tmp_path / "out")


def test_missing_manifest_points_to_manual_instructions(tmp_path: Path) -> None:
    with pytest.raises(PublicDataError, match="SOURCES.md"):
        verify_raw_files(tmp_path)


def test_manifest_missing_a_source(raw_copy: Path) -> None:
    manifest = json.loads((raw_copy / "manifest.json").read_text())
    del manifest["files"]["cms_footnote_crosswalk"]
    (raw_copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(PublicDataError, match="missing sources"):
        verify_raw_files(raw_copy)


def test_missing_raw_file(raw_copy: Path) -> None:
    (raw_copy / "cms_footnote_crosswalk.csv").unlink()
    with pytest.raises(PublicDataError, match="raw file missing"):
        verify_raw_files(raw_copy)


# --- Download (with a fake network) -------------------------------------------------------


def _fake_fetch(urls_seen: list[str], fail_on: str | None = None):  # noqa: ANN202
    def fetch(url: str) -> bytes:
        urls_seen.append(url)
        if fail_on and fail_on in url:
            raise OSError("HTTP Error 403: Forbidden")
        if "metastore" in url:
            dataset = url.rsplit("/", 1)[-1]
            return json.dumps(
                {
                    "modified": "2026-10-19",
                    "distribution": [
                        {
                            "mediaType": "text/csv",
                            "downloadURL": f"https://cms.example/{dataset}.csv",
                        }
                    ],
                }
            ).encode()
        if url.endswith(".json"):
            return json.dumps({"rowsUpdatedAt": 1792800000}).encode()
        return f"col\n{url}\n".encode()

    return fetch


def test_download_resolves_current_urls_and_fingerprints(tmp_path: Path) -> None:
    seen: list[str] = []
    manifest = download_sources(tmp_path, fetch=_fake_fetch(seen), today=date(2026, 11, 15))
    assert set(manifest.files) == set(SOURCES)
    cms = manifest.files[CMS_TIMELY_EFFECTIVE_CARE.key]
    assert cms.download_url == "https://cms.example/yv7e-xc69.csv"
    assert cms.source_last_modified == "2026-10-19"
    nys = manifest.files[NYS_ED_ENCOUNTERS_BY_FACILITY.key]
    assert nys.download_url.endswith("/5gzv-zv2z/rows.csv?accessType=DOWNLOAD")
    assert nys.sha256 == _sha(tmp_path / nys.raw_filename)
    verify_raw_files(tmp_path)  # what was written matches the manifest


def test_download_failure_gives_manual_instructions(tmp_path: Path) -> None:
    with pytest.raises(PublicDataError) as excinfo:
        download_sources(tmp_path, fetch=_fake_fetch([], fail_on="yv7e-xc69"))
    message = str(excinfo.value)
    assert "403" in message
    assert CMS_TIMELY_EFFECTIVE_CARE.landing_page in message
    assert "register cms_timely_effective_care" in message


def test_empty_download_is_rejected(tmp_path: Path) -> None:
    def fetch(url: str) -> bytes:
        return _fake_fetch([])(url) if ("metastore" in url or url.endswith(".json")) else b""

    with pytest.raises(PublicDataError, match="downloaded file is empty"):
        download_sources(tmp_path, fetch=fetch)


@pytest.mark.parametrize("count", [0, 2])
def test_cms_catalog_must_offer_exactly_one_csv(count: int) -> None:
    dist = [{"mediaType": "text/csv", "downloadURL": "u"}] * count
    with pytest.raises(PublicDataError, match="expected one CSV"):
        resolve_download(
            CMS_TIMELY_EFFECTIVE_CARE, lambda _: json.dumps({"distribution": dist}).encode()
        )


# --- Register a manually downloaded file -----------------------------------------------------


def test_register_local_file(tmp_path: Path) -> None:
    source = PUBLIC_FIXTURE_RAW / "cms_footnote_crosswalk.csv"
    entry = register_local_file(
        "cms_footnote_crosswalk",
        source,
        download_url="https://data.cms.gov/example/Footnote_Crosswalk.csv",
        retrieved_on=date(2026, 11, 15),
        raw_dir=tmp_path,
    )
    assert entry.sha256 == _sha(source)
    assert (tmp_path / "cms_footnote_crosswalk.csv").read_bytes() == source.read_bytes()
    register_local_file(
        "cms_hospital_general_information",
        PUBLIC_FIXTURE_RAW / "cms_hospital_general_information.csv",
        download_url="https://data.cms.gov/example/hgi.csv",
        retrieved_on=date(2026, 11, 15),
        raw_dir=tmp_path,
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert set(manifest["files"]) == {"cms_footnote_crosswalk", "cms_hospital_general_information"}


@pytest.mark.parametrize(
    ("key", "filename", "message"),
    [
        ("not_a_source", "cms_footnote_crosswalk.csv", "unknown source key"),
        ("cms_footnote_crosswalk", "does_not_exist.csv", "file not found"),
    ],
)
def test_register_rejects_bad_input(tmp_path: Path, key: str, filename: str, message: str) -> None:
    with pytest.raises(PublicDataError, match=message):
        register_local_file(
            key,
            PUBLIC_FIXTURE_RAW / filename,
            download_url="u",
            retrieved_on=date(2026, 11, 15),
            raw_dir=tmp_path,
        )


def test_register_rejects_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    with pytest.raises(PublicDataError, match="is empty"):
        register_local_file(
            "cms_footnote_crosswalk",
            empty,
            download_url="u",
            retrieved_on=date(2026, 11, 15),
            raw_dir=tmp_path / "raw",
        )


# --- The committed processed data ---------------------------------------------------------------


def test_committed_processed_data_is_valid() -> None:
    m = load_processed_measures(PROCESSED_DIR / MEASURES_FILE)
    assert len(m) == 470
    assert (m["provenance"] == "PUBLIC").all()
    assert m.loc[m["status"] != "reported", "value"].isna().all()
    manifest = json.loads((PROCESSED_DIR / BUILD_MANIFEST_FILE).read_text())
    assert manifest["outputs"][MEASURES_FILE]["sha256"] == _sha(PROCESSED_DIR / MEASURES_FILE)
    assert manifest["outputs"][FACILITIES_FILE]["sha256"] == _sha(PROCESSED_DIR / FACILITIES_FILE)


def test_nys_counts_for_unregistered_facility_raise(raw_copy: Path, tmp_path: Path) -> None:
    # Remove one Bronx hospital from the registry excerpt; its NYS ED rows remain.
    registry = raw_copy / "nys_health_facility_general_information.csv"
    lines = registry.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = [ln for ln in lines if not ln.startswith("1186,")]
    assert len(kept) == len(lines) - 1
    registry.write_text("".join(kept), encoding="utf-8")
    manifest = json.loads((raw_copy / "manifest.json").read_text())
    entry = manifest["files"]["nys_health_facility_general_information"]
    entry["sha256"], entry["bytes"] = _sha(registry), registry.stat().st_size
    (raw_copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(PublicDataError, match="missing from the registry: \\['001186'\\]"):
        build_bronx_reference(raw_copy, CROSSWALK_PATH, tmp_path / "out")


def test_output_schema_is_exactly_the_documented_aggregate_columns() -> None:
    from beyond_the_wait.data.public_pipeline import MEASURE_COLUMNS

    m = load_processed_measures(PROCESSED_DIR / MEASURES_FILE)
    assert list(m.columns) == MEASURE_COLUMNS
    # Facility-level aggregates only: no patient-level fields of any kind.
    patient_like = {"patient", "mrn", "dob", "birth", "name_last", "ssn", "encounter_id"}
    assert not {c for c in m.columns if any(p in c.lower() for p in patient_like)}
