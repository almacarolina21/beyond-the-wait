# Dashboard — Beyond the Wait (Milestone 7)

An interactive Streamlit + Plotly app that presents the project to healthcare
leaders and technical reviewers. **No Claude API, no API key, no internet
connection needed** once the data files below exist.

```bash
source .venv/bin/activate
python -m beyond_the_wait.analysis.whatif_demo --seeds 30 --sensitivity-seeds 20 --workers 8   # once, ~6 min
streamlit run app/streamlit_app.py
# then open http://localhost:8501
```

---

## 1. Pages and audiences

| Page | For | Data | Provenance |
|---|---|---|---|
| **Overview** | Everyone | Text only | Defines PUBLIC / SYNTHETIC / SIMULATED |
| **Bronx Public Data** | Leaders, reviewers | Committed Milestone 2 tables | **PUBLIC** only |
| **Fictional Patient Flow** | Leaders | One live single-seed run (≈1 s, cached), plus generated patients | **SYNTHETIC** inputs, **SIMULATED** outcomes |
| **What-If Lab** | Leaders | Precomputed 30-seed paired comparisons | **SIMULATED** |
| **Sensitivity, Validation & Methods** | Reviewers | Sensitivity grid, gate checks, CI vs. PI, limitations | **SIMULATED** |

## 2. Architecture and data flow

```
app/streamlit_app.py              navigation (st.navigation), theme, sidebar note
app/views/*.py                    thin page scripts: layout + text, no calculations
src/beyond_the_wait/dashboard/
    data.py    loaders and table shaping -> existing APIs only
    charts.py  Plotly figure builders (provenance in every title)
    theme.py   palette, Plotly template, badges, CSS
    ui.py      Streamlit helpers, caching, callouts
```

| Data | Source API | Verified by |
|---|---|---|
| PUBLIC | `load_processed_measures()` (Milestone 2) | `require_provenance(PUBLIC)` |
| One SIMULATED run | `run_simulation` → `validate_run` → `release_for_interpretation` | The Milestone 5 gate. BLOCKED shows no numbers. |
| Generated patients | `generate_patients` | `require_provenance(SYNTHETIC)` |
| Multi-seed results | `analysis/results_bundle.load_bundle()` | SHA-256 fingerprints, SIMULATED labels, valid statuses; BLOCKED comparisons removed and listed |

**No calculation logic lives in the UI.** The dashboard needed per-patient
boarding times, so `detailed_metrics.boarding_values()` was extracted and is
now shared by the existing summary and the box plot.

### The results bundle

`whatif_demo` writes `data/synthetic/experiments/` (git-ignored, reproducible):
`comparisons.csv`, `comparison_checks.csv`, `archetypes.csv`,
`sensitivity.csv`, `stability.csv`, and `manifest.json` (settings, seeds,
per-comparison status, carried CAUTIONs, lever effects, and a SHA-256 for
every file). The dashboard **refuses** the bundle if any file is missing,
altered, mislabeled, or has a BLOCKED comparison that still contains numbers.

### Caching

| What | Cached by | Why |
|---|---|---|
| Public data, results bundle | path + file modification time | Rebuilding a file reloads it automatically |
| Live run | (seed, days), max 8 entries | About 1 s per run; days capped at 14 |
| 30-seed experiments | **never run by the app** | Precomputed only |

## 3. Safeguards against misinterpretation

- Every chart title starts with **PUBLIC**, **SYNTHETIC**, or **SIMULATED**.
  Public and simulated data are on separate pages and never share a chart.
- Unavailable and suppressed public values appear as **text** ("Not available
  [footnote 1]", "Suppressed (S)"), never as zero bars.
- The page states reporting periods (OP-18: CY 2025; OP-22 and EDV: CY 2024)
  and warns against comparing them or ranking hospitals.
- Effects are a dot plus the **95% CI of the mean difference**, with the zero
  line marked. "Lower in the model" is a neutral blue, not a green "safe" light.
- Utilization changes are labeled "changed", because lower is neither better
  nor worse by itself.
- When an intervention shortens one stage but lengthens another, a callout
  says that congestion moved.
- The **ESI-1 limitation** appears on the Patient Flow and Methods pages; the
  "not a recommendation" note appears on the Overview and the What-If Lab.
- BLOCKED comparisons are listed in an error and can't be selected or plotted.
  CAUTION reasons are shown per comparison.
- Sensitivity cells in an overload regime are marked **stress** and excluded
  from stability verdicts.

## 4. Visual design

- Restrained light theme (`.streamlit/config.toml`), system font stack (works
  offline), 1,180 px content width, spacious line height, cards for concepts.
- Colors come from the documented reference palette of the project's
  data-visualization method, used unmodified:
  - categorical slots in fixed order for stages and series;
  - a one-hue blue ordinal ramp for ESI (darker = more urgent);
  - status colors reserved for PASS / CAUTION / BLOCKED, always with an icon and a word.
- **The palette validator was not run**: Node.js isn't installed on the
  development machine. To compensate, every mark also has a text label, hover
  text, or legend entry, so no meaning depends on color alone.
- One y-axis per chart; legends for 2+ series; units in axis titles.
- Narrow screens: Streamlit columns stack, and the CSS reduces side padding below 700 px.

## 5. Security and reliability

- No API keys, no Claude calls, no file uploads (`maxUploadSize = 1`), no YAML
  upload or arbitrary file execution. Data paths are fixed (or set by the
  `BTW_RESULTS_DIR` / `BTW_PUBLIC_DIR` environment variables, used in tests).
- `server.address = "localhost"`: the app isn't reachable from the local network.
- `browser.gatherUsageStats = false`: no Streamlit telemetry.
- Missing or invalid files produce a clear message and the command to fix it, not a crash.

## 6. Testing

- **Data layer:** provenance verification, unavailable/suppressed values,
  periods, units, live run, BLOCKED live run, run cap, bundle helpers.
- **Results bundle:** fingerprint tampering, wrong provenance, missing files
  and columns, bad version and status, BLOCKED comparisons excluded (and
  rejected if they carry numbers), BLOCKED sensitivity cells lose their numbers.
- **Charts:** provenance titles, no real hospital names in simulated charts,
  values matching the source, error bars equal to the CI, percentage-point
  scaling, prediction interval wider than the CI, empty inputs.
- **Pages (Streamlit `AppTest`, headless):** every page runs without
  exceptions; the missing bundle shows build instructions; a tampered bundle
  is refused; a BLOCKED comparison is listed but not selectable or plotted;
  selectors update the content; safety messages are present.
- **Live server check:** the health endpoint returns `ok` at `http://localhost:8501`
  and the network address is refused.

## 7. Visual inspection: not yet done

The pages have **not** been inspected visually: no browser or screenshot tool
was used during development. Before demonstrating, open each page and check:

1. Chart titles and subtitles fit without overlap; hover tooltips are readable.
2. Public bar charts show "Not available" text where values are missing.
3. Forest plots: dots, interval bars, and the dotted zero line are visible; the legend doesn't cover data.
4. The sensitivity heatmap's cell text is readable on every cell color.
5. Tables don't overflow horizontally at your usual window width; try a narrow window too.
6. The ESI-1 safety callout is prominent but not alarming.
