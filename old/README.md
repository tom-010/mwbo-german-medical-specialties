# old/ — Original parsing scripts (archived)

**Do not use these scripts.** They are kept for historical reference only.
The unified replacement is `pipeline.py` in the project root.

## What happened here

The MWBO PDF was converted to a DuckDB database in a multi-step process that
grew organically. The docx-to-HTML split and the JSON-to-DuckDB assembly lived
in a separate repo (`dx-agents/`), while the HTML-to-JSON parsing was done by
8 different scripts in `mwbo_scripts/`, each handling a different batch of files.

The full original pipeline was:

```
mwbo.docx
  │  ../dx-agents/split_mwbo.py (pandoc + BeautifulSoup)
  ▼
gebiete/*.html  (119 files)
  │  8 scripts in mwbo_scripts/ (batched by file subsets)
  ▼
gebiete/*.json  (119 files)
  │  ../dx-agents/target_schema.py to-json
  ▼
catalog.json
  │  ../dx-agents/target_schema.py to-duckdb
  ▼
catalog.duckdb
```

## What's in this directory

### `gebiete/`
119 HTML files (split from mwbo.docx) and their corresponding JSON files.
Each file represents one Facharzt, Schwerpunkt, or Zusatz-Weiterbildung section.

### `gebiete.py`
Pydantic models for the intermediate per-file JSON schema (German field names:
`typ`, `gebiet`, `bezeichnung`, `inhalte`, etc.).

### `mwbo_scripts/`
The 8 HTML-to-JSON parsing scripts. Each processes a hardcoded subset of files
and has slightly different parsing logic (some use regex, some BeautifulSoup):

| Script | Files | Type |
|---|---|---|
| `convert_mwbo.py` | 6 gebiet files (allgemeinmedizin–biochemie) | facharzt |
| `parse_mwbo.py` | 12 gebiet files (chirurgie, frauenheilkunde, HNO, dermatologie) | facharzt, schwerpunkt |
| `parse_mwbo_new.py` | 7 gebiet files (first batch + chirurgie schwerpunkt) | facharzt, schwerpunkt |
| `convert_new8.py` | 8 gebiet files (pathologie–psychiatrie) | facharzt, schwerpunkt |
| `convert_mwbo_to_json.py` | 11 gebiet files (physiologie–urologie) | facharzt, schwerpunkt |
| `convert_html_to_json.py` | 12 zusatz files (tumortherapie–psychotherapie) | zusatz-weiterbildung |
| `convert_zusatz.py` | 6 zusatz files (haemostaseologie–kardiale-MRT) | zusatz-weiterbildung |
| `convert_8_files.py` | 8 zusatz files (allergologie–kinder-orthopaedie) | zusatz-weiterbildung |

~22 HTML files are not in any script's explicit file list — they were processed
by re-running scripts with expanded lists during development.

All scripts reference `/home/tom/Projects/health_bot/gebiete` (the project's
former location).

### `catalog.json` / `catalog.duckdb`
The final outputs of the original pipeline. `catalog.duckdb` was the reference
database used to validate the new `pipeline.py`.

### `catalog2.json` / `catalog2.html` / `catalog2.duckdb`
Test outputs from the new `pipeline.py`, kept to document the comparison results.

### `pipeline.md`
Detailed reconstruction of the original pipeline with script-to-file mapping,
identified gaps, and the refactoring plan that led to `pipeline.py`.

### `main.py`
Placeholder entry point (unused).

## Known issues in the original pipeline

1. **Bogus medical field** — "Chirurgie – Facharzt Orthopädie und Unfallchirurgie"
   was created as a separate medical field because the en-dash in the H3 heading
   wasn't stripped. Fixed in `pipeline.py`.

2. **Lost knowledge items** — `convert_zusatz.py` treated column-1-only rows as
   section headings instead of knowledge items, silently dropping ~200 competency
   entries. Fixed in `pipeline.py`.

3. **Inflated section counts** — the same script set each row's column-1 text as
   the `abschnitt`, creating one CompetencySection per row instead of grouping
   items under their actual section headings. Fixed in `pipeline.py`.
