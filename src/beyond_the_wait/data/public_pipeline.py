"""Reproducible pipeline: official public files -> Bronx ED reference tables.

Usage (from the repository root):
    python -m beyond_the_wait.data.public_pipeline download   # needs internet
    python -m beyond_the_wait.data.public_pipeline build      # offline

download
    Looks up each dataset's CURRENT file URL in the publisher's catalog,
    downloads it to data/public/raw/ (git-ignored), and writes
    data/public/raw/manifest.json: URL, SHA-256 fingerprint, size, download
    date, and the publisher's "last modified" date.

build
    Verifies every raw file still matches its fingerprint, validates the
    facility crosswalk against the official registries, loads and validates
    the measures, and writes to data/public/processed/:
        bronx_ed_measures.csv    one row per facility x measure x period
        bronx_facilities.csv     which facilities are included, and why
        build_manifest.json      inputs, parameters, output fingerprints

Reproducibility: outputs are sorted, use fixed formatting, and contain no
build timestamp, so the same inputs always produce byte-identical outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path

import pandas as pd

from beyond_the_wait.data.cms_loader import load_cms_ed_measures, load_cms_hospital_info
from beyond_the_wait.data.errors import PublicDataError
from beyond_the_wait.data.facility_matching import (
    bronx_nys_hospitals,
    load_crosswalk,
    related_sites,
    select_bronx_cms_hospitals,
    validate_crosswalk,
)
from beyond_the_wait.data.nys_loader import load_nys_ed_encounters, load_nys_facilities
from beyond_the_wait.data.provenance import (
    PROVENANCE_COLUMNS,
    Provenance,
    require_provenance,
    tag_dataframe,
)
from beyond_the_wait.data.public_sources import (
    CMS_FOOTNOTE_CROSSWALK,
    CMS_HOSPITAL_GENERAL_INFO,
    CMS_TIMELY_EFFECTIVE_CARE,
    NYS_ED_ENCOUNTERS_BY_FACILITY,
    NYS_HEALTH_FACILITY_INFO,
    NYS_ROWS_CSV_URL,
    SOURCES,
    PublicSource,
    RawManifest,
    SourceFile,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "public" / "raw"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "public" / "processed"
DEFAULT_CROSSWALK = REPO_ROOT / "data" / "public" / "reference" / "bronx_facility_crosswalk.csv"

MANIFEST_NAME = "manifest.json"
MEASURES_FILE = "bronx_ed_measures.csv"
FACILITIES_FILE = "bronx_facilities.csv"
BUILD_MANIFEST_FILE = "build_manifest.json"

COUNTY = "Bronx"
# NYS years kept: those overlapping the current CMS reporting periods
# (OP-22/EDV: calendar 2024; OP-18: calendar 2025).
NYS_YEARS: tuple[int, ...] = (2024, 2025)

MEASURE_COLUMNS: list[str] = [
    "source_key", "facility_id_type", "facility_id", "cms_ccn", "nys_pfi", "facility_name",
    "measure_id", "measure_name", "measure_kind", "unit",
    "period_start", "period_end", "period_type",
    "score_raw", "status", "value", "value_hours", "value_fraction", "value_category",
    "sample_raw", "sample_size", "footnote_codes", "footnote_text", "missing_reason",
    *PROVENANCE_COLUMNS,
]  # fmt: skip

Fetch = Callable[[str], bytes]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def http_get(url: str, timeout: int = 180) -> bytes:  # pragma: no cover - network
    request = urllib.request.Request(url, headers={"User-Agent": "beyond-the-wait/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def resolve_download(source: PublicSource, fetch: Fetch) -> tuple[str, str | None]:
    """Ask the publisher's catalog for the current file URL and last-modified date."""
    meta = json.loads(fetch(source.metadata_url))
    if source.portal == "cms_provider_data":
        csvs = [d for d in meta.get("distribution", []) if d.get("mediaType") == "text/csv"]
        if len(csvs) != 1:
            raise PublicDataError(f"{source.key}: expected one CSV distribution, found {len(csvs)}")
        return csvs[0]["downloadURL"], meta.get("modified")
    updated = meta.get("rowsUpdatedAt")
    modified = datetime.fromtimestamp(updated).date().isoformat() if updated else None
    return NYS_ROWS_CSV_URL.format(id=source.dataset_id), modified


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manual_download_instructions(source: PublicSource) -> str:
    """Exact steps to obtain one official file by hand."""
    return (
        f"Manual download for {source.key}:\n"
        f"  1. Open {source.landing_page}\n"
        f"  2. Download the CSV for '{source.title}' ({source.publisher}).\n"
        f"  3. Register it (records URL, fingerprint, and date):\n"
        f"     python -m beyond_the_wait.data.public_pipeline register {source.key} "
        f"<path-to-csv> --url <the CSV's download URL>"
    )


def _write_manifest(raw_dir: Path, manifest: RawManifest) -> None:
    (raw_dir / MANIFEST_NAME).write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def download_sources(
    raw_dir: Path = DEFAULT_RAW_DIR, *, fetch: Fetch = http_get, today: date | None = None
) -> RawManifest:
    """Download every registered source and write the raw manifest.

    If any download fails, stop with instructions for downloading that file
    manually. We never substitute a different or invented file.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    retrieved = today or date.today()
    files: dict[str, SourceFile] = {}
    for source in SOURCES.values():
        try:
            url, modified = resolve_download(source, fetch)
            data = fetch(url)
        except (OSError, ValueError, KeyError) as exc:  # URLError/HTTPError are OSErrors
            raise PublicDataError(
                f"could not download {source.title}: {exc}\n{manual_download_instructions(source)}"
            ) from exc
        if not data:
            raise PublicDataError(
                f"{source.key}: downloaded file is empty ({url})\n"
                f"{manual_download_instructions(source)}"
            )
        (raw_dir / source.raw_filename).write_bytes(data)
        files[source.key] = SourceFile(
            key=source.key,
            dataset_id=source.dataset_id,
            download_url=url,
            raw_filename=source.raw_filename,
            sha256=sha256_bytes(data),
            bytes=len(data),
            retrieved_on=retrieved,
            source_last_modified=modified,
        )
    manifest = RawManifest(files=files)
    _write_manifest(raw_dir, manifest)
    return manifest


def register_local_file(
    key: str,
    path: Path,
    *,
    download_url: str,
    retrieved_on: date,
    source_last_modified: str | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> SourceFile:
    """Record a manually downloaded official file in the raw manifest.

    The file is copied into raw_dir under its standard name and fingerprinted,
    so the build step treats it exactly like an automated download.
    """
    if key not in SOURCES:
        raise PublicDataError(f"unknown source key {key!r}; expected one of {sorted(SOURCES)}")
    if not path.is_file():
        raise PublicDataError(f"file not found: {path}")
    source = SOURCES[key]
    data = path.read_bytes()
    if not data:
        raise PublicDataError(f"{path} is empty")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / source.raw_filename).write_bytes(data)
    entry = SourceFile(
        key=key,
        dataset_id=source.dataset_id,
        download_url=download_url,
        raw_filename=source.raw_filename,
        sha256=sha256_bytes(data),
        bytes=len(data),
        retrieved_on=retrieved_on,
        source_last_modified=source_last_modified,
    )
    manifest_path = raw_dir / MANIFEST_NAME
    files = (
        dict(RawManifest.model_validate_json(manifest_path.read_text("utf-8")).files)
        if manifest_path.is_file()
        else {}
    )
    files[key] = entry
    _write_manifest(raw_dir, RawManifest(files=files))
    return entry


def verify_raw_files(raw_dir: Path) -> RawManifest:
    """Load the raw manifest and confirm every file matches its fingerprint."""
    path = raw_dir / MANIFEST_NAME
    if not path.is_file():
        raise PublicDataError(
            f"no raw manifest at {path}. Run the 'download' step first, or see "
            "data/public/SOURCES.md for manual download instructions."
        )
    manifest = RawManifest.model_validate_json(path.read_text(encoding="utf-8"))
    missing_sources = sorted(set(SOURCES) - set(manifest.files))
    if missing_sources:
        raise PublicDataError(f"raw manifest is missing sources: {missing_sources}")
    for entry in manifest.files.values():
        file_path = raw_dir / entry.raw_filename
        if not file_path.is_file():
            raise PublicDataError(f"raw file missing: {file_path}")
        actual = sha256_bytes(file_path.read_bytes())
        if actual != entry.sha256:
            raise PublicDataError(
                f"{entry.raw_filename} does not match its manifest fingerprint "
                f"(expected {entry.sha256[:12]}..., found {actual[:12]}...). "
                "The file changed after download; re-download instead of editing raw files."
            )
    return manifest


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _tag(df: pd.DataFrame, source: PublicSource, manifest: RawManifest) -> pd.DataFrame:
    entry = manifest.files[source.key]
    return tag_dataframe(
        df,
        label=Provenance.PUBLIC,
        dataset_id=source.dataset_id,
        source_url=entry.download_url,
        retrieved_on=entry.retrieved_on,
    )


def _write_csv(df: pd.DataFrame, path: Path) -> str:
    text = df.to_csv(index=False, lineterminator="\n")
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_facilities_table(
    cms_bronx: pd.DataFrame,
    crosswalk: pd.DataFrame,
    registry: pd.DataFrame,
    cms_ids_with_measures: set[str],
    nys_ids_with_counts: set[str],
) -> pd.DataFrame:
    by_pfi = registry.set_index("pfi")
    xwalk = crosswalk.set_index("cms_ccn")
    rows = []
    for _, c in cms_bronx.iterrows():
        ccn = c["Facility ID"]
        row = {
            "status": "matched" if c["included"] else "excluded",
            "cms_ccn": ccn,
            "cms_facility_name": c["Facility Name"],
            "cms_address": c["Address"],
            "cms_zip": c["ZIP Code"],
            "cms_hospital_type": c["Hospital Type"],
            "cms_emergency_services": c["Emergency Services"],
            "in_cms_ed_measures": ccn in cms_ids_with_measures,
            "nys_pfi": "",
            "nys_facility_name": "",
            "nys_address": "",
            "nys_operating_certificate": "",
            "nys_sites_listing_this_as_main_site": "",
            "in_nys_ed_counts": False,
            "match_method": "",
            "note": c["exclusion_reason"],
        }
        if c["included"]:
            x = xwalk.loc[ccn]
            n = by_pfi.loc[x["nys_pfi"]]
            row |= {
                "nys_pfi": x["nys_pfi"],
                "nys_facility_name": n["Facility Name"],
                "nys_address": n["Facility Address 1"],
                "nys_operating_certificate": n["Operating Certificate Number"],
                "nys_sites_listing_this_as_main_site": "; ".join(
                    f"{p} {name}" for p, name in related_sites(x["nys_pfi"], registry)
                ),
                "in_nys_ed_counts": x["nys_pfi"] in nys_ids_with_counts,
                "match_method": x["match_method"],
                "note": x["review_note"],
            }
        rows.append(row)

    matched_pfis = set(crosswalk["nys_pfi"])
    # Every NYS Bronx general hospital, plus any other site that appears in the
    # NYS ED counts (e.g., a hospital extension clinic), so each facility in
    # the measures table is explained here.
    nys_sites = pd.concat(
        [bronx_nys_hospitals(registry), registry[registry["pfi"].isin(nys_ids_with_counts)]]
    ).drop_duplicates("pfi")
    unknown = sorted(nys_ids_with_counts - set(registry["pfi"]))
    if unknown:
        raise PublicDataError(f"NYS ED counts reference PFIs missing from the registry: {unknown}")
    for _, n in nys_sites.sort_values("pfi").iterrows():
        if n["pfi"] in matched_pfis:
            continue
        main = n["main_site_pfi"]
        note = (
            f"No separate CMS Bronx acute-care ED record matched this NYS site "
            f"(NYS type: {n['Short Description']})."
        )
        if main:
            note += f" NYS lists its main site as {main} {by_pfi.loc[main, 'Facility Name']}."
        rows.append(
            {
                "status": "nys_only",
                "cms_ccn": "",
                "cms_facility_name": "",
                "cms_address": "",
                "cms_zip": "",
                "cms_hospital_type": "",
                "cms_emergency_services": "",
                "in_cms_ed_measures": False,
                "nys_pfi": n["pfi"],
                "nys_facility_name": n["Facility Name"],
                "nys_address": n["Facility Address 1"],
                "nys_operating_certificate": n["Operating Certificate Number"],
                "nys_sites_listing_this_as_main_site": "; ".join(
                    f"{p} {name}" for p, name in related_sites(n["pfi"], registry)
                ),
                "in_nys_ed_counts": n["pfi"] in nys_ids_with_counts,
                "match_method": "",
                "note": note,
            }
        )
    table = pd.DataFrame(rows)
    order = {"matched": 0, "excluded": 1, "nys_only": 2}
    return (
        table.assign(_o=table["status"].map(order))
        .sort_values(["_o", "cms_ccn", "nys_pfi"])
        .drop(columns="_o")
        .reset_index(drop=True)
    )


def build_bronx_reference(
    raw_dir: Path = DEFAULT_RAW_DIR,
    crosswalk_path: Path = DEFAULT_CROSSWALK,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict[str, object]:
    """Build the processed Bronx ED reference tables. Returns the build manifest."""
    manifest = verify_raw_files(raw_dir)

    def raw(source: PublicSource) -> Path:
        return raw_dir / manifest.files[source.key].raw_filename

    cms_bronx = select_bronx_cms_hospitals(load_cms_hospital_info(raw(CMS_HOSPITAL_GENERAL_INFO)))
    registry = load_nys_facilities(raw(NYS_HEALTH_FACILITY_INFO))
    crosswalk = load_crosswalk(crosswalk_path)
    validate_crosswalk(crosswalk, cms_bronx, registry)
    ccn_to_pfi = dict(zip(crosswalk["cms_ccn"], crosswalk["nys_pfi"], strict=True))
    pfi_to_ccn = {v: k for k, v in ccn_to_pfi.items()}

    cms, cms_report = load_cms_ed_measures(
        raw(CMS_TIMELY_EFFECTIVE_CARE), raw(CMS_FOOTNOTE_CROSSWALK), facility_ids=ccn_to_pfi
    )
    cms = cms.assign(
        source_key=CMS_TIMELY_EFFECTIVE_CARE.key,
        facility_id_type="cms_ccn",
        cms_ccn=cms["facility_id"],
        nys_pfi=cms["facility_id"].map(ccn_to_pfi),
    )
    cms = _tag(cms, CMS_TIMELY_EFFECTIVE_CARE, manifest)

    nys, nys_report = load_nys_ed_encounters(
        raw(NYS_ED_ENCOUNTERS_BY_FACILITY), county=COUNTY, years=NYS_YEARS
    )
    nys = nys.assign(
        source_key=NYS_ED_ENCOUNTERS_BY_FACILITY.key,
        facility_id_type="nys_pfi",
        cms_ccn=nys["facility_id"].map(pfi_to_ccn).fillna(""),
        nys_pfi=nys["facility_id"],
    )
    nys = _tag(nys, NYS_ED_ENCOUNTERS_BY_FACILITY, manifest)

    measures = pd.concat(
        [cms.reindex(columns=MEASURE_COLUMNS), nys.reindex(columns=MEASURE_COLUMNS)],
        ignore_index=True,
    )
    measures["value"] = pd.to_numeric(measures["value"]).astype("Float64")
    for col in ("value_hours", "value_fraction"):
        measures[col] = pd.to_numeric(measures[col]).astype("Float64").round(6)
    measures["sample_size"] = pd.to_numeric(measures["sample_size"]).astype("Int64")
    for col in ("period_start", "period_end"):
        measures[col] = measures[col].map(lambda d: d.isoformat())
    measures = measures.sort_values(
        ["source_key", "facility_id", "measure_id", "period_start", "period_type"]
    ).reset_index(drop=True)

    require_provenance(measures, Provenance.PUBLIC)
    leaked = measures[(measures["status"] != "reported") & measures["value"].notna()]
    if not leaked.empty:  # pragma: no cover - guarded by loaders; defense in depth
        raise PublicDataError("a missing/suppressed measurement acquired a value")

    facilities = _build_facilities_table(
        cms_bronx, crosswalk, registry, set(cms["facility_id"]), set(nys["facility_id"])
    )
    facilities = tag_dataframe(
        facilities,
        label=Provenance.PUBLIC,
        dataset_id=f"{CMS_HOSPITAL_GENERAL_INFO.dataset_id};{NYS_HEALTH_FACILITY_INFO.dataset_id}",
        source_url=(
            f"{manifest.files[CMS_HOSPITAL_GENERAL_INFO.key].download_url};"
            f"{manifest.files[NYS_HEALTH_FACILITY_INFO.key].download_url}"
        ),
        retrieved_on=manifest.files[CMS_HOSPITAL_GENERAL_INFO.key].retrieved_on,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    build_manifest: dict[str, object] = {
        "builder": "beyond_the_wait.data.public_pipeline",
        "package_version": version("beyond-the-wait"),
        "parameters": {"county": COUNTY, "nys_years": list(NYS_YEARS)},
        "inputs": {
            key: {
                "dataset_id": f.dataset_id,
                "download_url": f.download_url,
                "sha256": f.sha256,
                "retrieved_on": f.retrieved_on.isoformat(),
                "source_last_modified": f.source_last_modified,
            }
            for key, f in sorted(manifest.files.items())
        },
        "crosswalk_sha256": sha256_bytes(crosswalk_path.read_bytes()),
        "reports": {"cms": cms_report, "nys": nys_report},
        "outputs": {
            MEASURES_FILE: {
                "sha256": _write_csv(measures, out_dir / MEASURES_FILE),
                "rows": len(measures),
            },
            FACILITIES_FILE: {
                "sha256": _write_csv(facilities, out_dir / FACILITIES_FILE),
                "rows": len(facilities),
            },
        },
    }
    (out_dir / BUILD_MANIFEST_FILE).write_text(
        json.dumps(build_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return build_manifest


def load_processed_measures(path: Path = DEFAULT_OUT_DIR / MEASURES_FILE) -> pd.DataFrame:
    """Read the processed measures table, keeping IDs as text and blanks as missing."""
    df = pd.read_csv(
        path,
        dtype={"facility_id": str, "cms_ccn": str, "nys_pfi": str, "footnote_codes": str},
        keep_default_na=False,
        na_values={"value": [""], "value_hours": [""], "value_fraction": [""], "sample_size": [""]},
    )
    require_provenance(df, Provenance.PUBLIC)
    return df


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download", help="download all sources (needs internet)")
    sub.add_parser("build", help="build processed tables from raw files (offline)")
    reg = sub.add_parser("register", help="record a manually downloaded official file")
    reg.add_argument("key", choices=sorted(SOURCES))
    reg.add_argument("path", type=Path)
    reg.add_argument("--url", required=True, help="official download URL of the file")
    reg.add_argument("--retrieved-on", type=date.fromisoformat, default=date.today())
    reg.add_argument("--source-modified", default=None, help="publisher's last-modified date")
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            f = register_local_file(
                args.key,
                args.path,
                download_url=args.url,
                retrieved_on=args.retrieved_on,
                source_last_modified=args.source_modified,
            )
            print(f"registered {f.raw_filename}: {f.bytes:,} bytes sha256={f.sha256[:12]}...")
        elif args.command == "download":
            m = download_sources()
            for f in m.files.values():
                print(f"downloaded {f.raw_filename}: {f.bytes:,} bytes sha256={f.sha256[:12]}...")
        else:
            result = build_bronx_reference()
            for name, info in result["outputs"].items():  # type: ignore[union-attr]
                print(f"wrote {name}: {info['rows']} rows sha256={info['sha256'][:12]}...")
    except PublicDataError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
