"""Registry of the official public datasets used in this project.

Every source was verified on 2026-11-15 against the publisher's own catalog
API (CMS Provider Data Catalog metastore; NYS Health Data / Socrata views
API). See data/public/SOURCES.md for the human-readable version.

Download URLs: CMS publishes each release at a new URL, so we store the
*catalog* URL and look up the current file URL at download time. The URL
actually used is recorded in the download manifest, so every processed file
can be traced to the exact file it came from.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CMS_METASTORE_URL = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{id}"
NYS_VIEW_METADATA_URL = "https://health.data.ny.gov/api/views/{id}.json"
NYS_ROWS_CSV_URL = "https://health.data.ny.gov/api/views/{id}/rows.csv?accessType=DOWNLOAD"


class PublicSource(BaseModel):
    """One official dataset and how we use it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    publisher: str
    portal: Literal["cms_provider_data", "ny_health_data"]
    dataset_id: str
    landing_page: str
    raw_filename: str
    used_for: str
    data_dictionary_url: str | None = None

    @property
    def metadata_url(self) -> str:
        if self.portal == "cms_provider_data":
            return CMS_METASTORE_URL.format(id=self.dataset_id)
        return NYS_VIEW_METADATA_URL.format(id=self.dataset_id)


CMS_DICTIONARY = (
    "https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/"
    "hospital/HOSPITAL_Data_Dictionary.pdf"
)

CMS_TIMELY_EFFECTIVE_CARE = PublicSource(
    key="cms_timely_effective_care",
    title="Timely and Effective Care - Hospital",
    publisher="Centers for Medicare & Medicaid Services (CMS)",
    portal="cms_provider_data",
    dataset_id="yv7e-xc69",
    landing_page="https://data.cms.gov/provider-data/dataset/yv7e-xc69",
    raw_filename="cms_timely_effective_care_hospital.csv",
    used_for="ED measures OP-18a/b/c/d (median ED time), OP-22 (left without being seen), EDV.",
    data_dictionary_url=CMS_DICTIONARY,
)

CMS_FOOTNOTE_CROSSWALK = PublicSource(
    key="cms_footnote_crosswalk",
    title="Footnote Crosswalk",
    publisher="Centers for Medicare & Medicaid Services (CMS)",
    portal="cms_provider_data",
    dataset_id="y9us-9xdf",
    landing_page="https://data.cms.gov/provider-data/dataset/y9us-9xdf",
    raw_filename="cms_footnote_crosswalk.csv",
    used_for="Official text for footnote codes that explain missing or qualified scores.",
    data_dictionary_url=CMS_DICTIONARY,
)

CMS_HOSPITAL_GENERAL_INFO = PublicSource(
    key="cms_hospital_general_information",
    title="Hospital General Information",
    publisher="Centers for Medicare & Medicaid Services (CMS)",
    portal="cms_provider_data",
    dataset_id="xubh-q36u",
    landing_page="https://data.cms.gov/provider-data/dataset/xubh-q36u",
    raw_filename="cms_hospital_general_information.csv",
    used_for="Facility directory: county, hospital type, and whether emergency services exist.",
    data_dictionary_url=CMS_DICTIONARY,
)

NYS_HEALTH_FACILITY_INFO = PublicSource(
    key="nys_health_facility_general_information",
    title="Health Facility General Information",
    publisher="New York State Department of Health",
    portal="ny_health_data",
    dataset_id="vn5v-hh5r",
    landing_page="https://health.data.ny.gov/d/vn5v-hh5r",
    raw_filename="nys_health_facility_general_information.csv",
    used_for="Independent facility registry (PFI, address, operating certificate, main site).",
)

NYS_ED_ENCOUNTERS_BY_FACILITY = PublicSource(
    key="nys_sparcs_ed_encounters_by_facility",
    title=(
        "Hospital Emergency Department Discharges (SPARCS De-Identified): "
        "Emergency Department Encounters by Facility: Beginning 2018"
    ),
    publisher="New York State Department of Health",
    portal="ny_health_data",
    dataset_id="5gzv-zv2z",
    landing_page="https://health.data.ny.gov/d/5gzv-zv2z",
    raw_filename="nys_ed_encounters_by_facility.csv",
    used_for="Facility-level ED encounter COUNTS by quarter and year (aggregate, not visit-level).",
)

SOURCES: dict[str, PublicSource] = {
    s.key: s
    for s in (
        CMS_TIMELY_EFFECTIVE_CARE,
        CMS_FOOTNOTE_CROSSWALK,
        CMS_HOSPITAL_GENERAL_INFO,
        NYS_HEALTH_FACILITY_INFO,
        NYS_ED_ENCOUNTERS_BY_FACILITY,
    )
}


class SourceFile(BaseModel):
    """What was downloaded, from where, when, and its fingerprint (SHA-256)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    dataset_id: str
    download_url: str
    raw_filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=1)
    retrieved_on: date
    source_last_modified: str | None = None


class RawManifest(BaseModel):
    """Record of one download run. Stored as data/public/raw/manifest.json."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    files: dict[str, SourceFile]
