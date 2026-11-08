# must-quest-gps-distance

> **SYNTHETIC:** every ping, road, zone and number in this repository was generated for the MUST Company hiring Quest. None of it is real data.

A vehicle-advertising platform bills advertisers per kilometre driven inside a campaign zone, computed from 10-second GPS pings. This repo contains:
- a synthetic ping dataset with injected faults
- a deliberately naive "before" distance calculation
- a QA tool that finds where it goes wrong
- a corrected implementation that matches the independent truth within 1 m for every driver-day
- a regression suite that fails on the old code and passes on the new one

**Status:** project implementation and submission documents are complete and have been re-run locally. The Loom has been recorded. The required documents are [`intent.md`](intent.md) and [`directive.md`](directive.md).

## Quick start

Python 3.11+ (developed on 3.14). After dependencies are installed, the data and QA pipeline runs locally with no API keys. The interactive map loads Leaflet from a CDN, so viewing the map requires network access.

```powershell
python -m venv .venv
.\run.ps1 setup
.\run.ps1 all          # data -> qa -> verify -> evidence -> test + mutation check   (about 50 s)
```

On macOS/Linux with `make`: `python -m venv .venv && . .venv/bin/activate && make setup && make all`.

Then open `reports/report.html` and `reports/map.html`.

To see the regression run failing and then passing:

```powershell
.venv\Scripts\python -m pytest --runxfail -k reference_buggy   # old code: 12 failed, 6 controls passed
.venv\Scripts\python -m pytest -k distance_fixed                # new code: 18 passed
```

## Folder map

| Path | What |
|------|------|
| `data/` | `generate_data.py` (fixed seed, includes the independent truth code), `pings.csv`, `road.geojson`, `zone.geojson`, `fault_manifest.csv`, `truth_distance.csv`, `sanity_plot.png`. All **SYNTHETIC**; see `data/README.md` |
| `src/reference_buggy.py` | the "before" calculation (intentionally wrong) |
| `src/distance_fixed.py` | the corrected calculation (pure function) |
| `src/ping_rules.py`, `src/geo.py` | per-ping rules (R1–R6 plus the confirmed assumptions) and the local projection |
| `qa_check.py` | QA tool: `validation.csv`, `report.html`, `map.html`, `before_after.csv`, `waterfall_ranked.csv`, … |
| `verify_tool.py` | scores the QA tool against the fault manifest (precision/recall); the only reader of the manifest |
| `tests/` | pytest regression suite, parametrised over both implementations; `mutation_check.py`; `summarize_results.py` |
| `reports/` | generated outputs, including `test-results.xml` and `test-summary.md` |
| `docs/` | `assumptions.md` (CHECKPOINT 1 decisions, still not solved), `findings.md` (problems, ranking, root cause, fix, regression), `make_figures.py`, `findings_numbers.py`, `img/` |
| `intent.md`, `directive.md` | submission documents |
| `AI_LOG.md`, `EFFORT.md` | AI-use log (who caught what) and time tracking |
| `docs/ai/claude-code-brief.md` | archived brief given to Claude Code; its video instruction was updated to reflect the current plan to speak in my own words |
