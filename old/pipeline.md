# Pipeline: docx -> duckdb

This document reconstructs the current pipeline from the scripts as they exist today.
The goal is to understand the full flow, identify gaps, and then refactor into a clean,
reproducible pipeline.

## Overview

```
mwbo.docx
  │  split_mwbo.py (pandoc + BeautifulSoup)
  ▼
gebiete/*.html  (119 files: 71 gebiet-*.html + 48 zusatz-weiterbildung-*.html)
  │  8 different HTML→JSON scripts (batched by file subsets)
  ▼
gebiete/*.json  (119 files, one per specialty/qualification)
  │  target_schema.py to-json
  ▼
catalog.json    (single merged file with Pydantic validation)
  │  target_schema.py to-duckdb
  ▼
catalog.duckdb  (6 normalized tables)
```

## Step 1: docx -> HTML (split)

**Script:** `../dx-agents/split_mwbo.py`
**Input:** `mwbo.docx`
**Output:** `gebiete/*.html` (119 files)
**Dependencies:** `pandoc`, `beautifulsoup4`

Converts the docx to one big HTML via pandoc, then splits it at every `<h3>` heading
that starts with "Gebiet" or "Zusatz-Weiterbildung". Each section becomes its own
HTML file. Duplicate H3 texts (e.g. multiple "Gebiet Chirurgie" sections) are
disambiguated using the first H4 subtitle in that section.

**Note:** This script lives outside this repo (in `dx-agents/`).

## Step 2: HTML -> per-file JSON (parse)

There is no single script for this step. Instead, 8 scripts were written
incrementally, each handling a batch of files. They all do the same thing
conceptually (parse HTML tables into the intermediate JSON schema) but differ
in implementation details (regex vs BeautifulSoup, error handling, edge cases).

### Intermediate JSON schema (per file)

Defined by the Pydantic models in `gebiete.py`:

```
Weiterbildung:
  typ:                 "facharzt" | "schwerpunkt" | "zusatz-weiterbildung"
  gebiet:              str       # e.g. "Chirurgie"
  bezeichnung:         str       # e.g. "Facharzt/Fachärztin für Allgemeinchirurgie"
  zusatzbezeichnung:   str?      # e.g. "Hausarzt/Hausärztin"
  gebietsdefinition:   str?      # facharzt only
  definition:          str?      # zusatz-weiterbildung only
  voraussetzung:       str?      # schwerpunkt only
  mindestanforderungen: str?     # zusatz-weiterbildung only
  weiterbildungszeit:  str?
  inhalte:             list[Inhalt | Kursinhalt]
```

### Script-to-file mapping

The scripts were run in roughly this order (reconstructed from file lists in each script):

| # | Script | Location | Files processed | Type |
|---|--------|----------|-----------------|------|
| 1 | `convert_mwbo.py` | `mwbo_scripts/` | 6 gebiet files (allgemeinmedizin through biochemie) + chirurgie sub-files | facharzt |
| 2 | `parse_mwbo.py` | `mwbo_scripts/` | 12 gebiet files (chirurgie sub-specialties, frauenheilkunde, HNO, dermatologie) | facharzt, schwerpunkt |
| 3 | `parse_mwbo_new.py` | `mwbo_scripts/` | 7 gebiet files (allgemeinmedizin, anaesthesiologie, anatomie, arbeitsmedizin, augenheilkunde, biochemie, chirurgie-schwerpunkt) | facharzt, schwerpunkt |
| 4 | `convert_new8.py` | `mwbo_scripts/` | 8 gebiet files (pathologie through psychiatrie-schwerpunkt) | facharzt, schwerpunkt |
| 5 | `convert_mwbo_to_json.py` | `mwbo_scripts/` | 11 gebiet files (physiologie through urologie) | facharzt, schwerpunkt |
| 6 | `parse_8_gebiete.py` | `../dx-agents/` | 8 gebiet files (laboratoriumsmedizin through pathologie-neuropathologie) | facharzt |
| 7 | `convert_zusatz.py` | `mwbo_scripts/` | 6 zusatz files (haemostaseologie through kardiale-MRT) | zusatz-weiterbildung |
| 8 | `convert_8_files.py` | `mwbo_scripts/` | 8 zusatz files (allergologie through kinder-und-jugend-orthopaedie) | zusatz-weiterbildung |
| 9 | `convert_html_to_json.py` | `mwbo_scripts/` | 12 zusatz files (medikamentoese-tumortherapie through psychotherapie) | zusatz-weiterbildung |

**Note:** Some files were processed by multiple scripts during iteration (the later
run overwrote the earlier output). The scripts also reference a former path
(`/home/tom/Projects/health_bot/gebiete`) that predates the current repo structure.

### Files not explicitly listed in any script

The following HTML files (22 total) are not in any script's hardcoded file list.
They were likely processed by one of the scripts that was re-run with an updated
list, or converted in an ad-hoc session:

**Gebiete (gebiet-\*):**
- gebiet-humangenetik.html
- gebiet-hygiene-und-umweltmedizin.html
- gebiet-innere-medizin-* (10 files: all Innere Medizin sub-specialties)
- gebiet-kinder-und-jugendmedizin-* (10 files: all Kinder/Jugendmedizin incl. schwerpunkte)
- gebiet-kinder-und-jugendpsychiatrie-und-psychotherapie.html

**Zusatz-Weiterbildungen (zusatz-weiterbildung-\*):**
- zusatz-weiterbildung-aerztliches-qualitaetsmanagement.html
- zusatz-weiterbildung-akupunktur.html
- zusatz-weiterbildung-balneologie-und-medizinische-klimatologie.html
- zusatz-weiterbildung-ernaehrungsmedizin.html
- zusatz-weiterbildung-flugmedizin.html
- zusatz-weiterbildung-klinische-akut-und-notfallmedizin.html
- zusatz-weiterbildung-klinische-palliativmedizin.html
- zusatz-weiterbildung-krankenhaushygiene.html
- zusatz-weiterbildung-magnetresonanztomographie.html
- zusatz-weiterbildung-manuelle-medizin.html
- zusatz-weiterbildung-rehabilitationswesen.html
- zusatz-weiterbildung-roentgendiagnostik-fuer-nuklearmediziner.html
- zusatz-weiterbildung-schlafmedizin.html
- zusatz-weiterbildung-sexualmedizin.html
- zusatz-weiterbildung-sozialmedizin.html
- zusatz-weiterbildung-spezielle-kardiologie-fuer-erwachsene-mit-angeborenen-herzfehlern-emah.html
- zusatz-weiterbildung-spezielle-kinder-und-jugend-urologie.html
- zusatz-weiterbildung-spezielle-schmerztherapie.html
- zusatz-weiterbildung-sportmedizin.html
- zusatz-weiterbildung-suchtmedizinische-grundversorgung.html
- zusatz-weiterbildung-transplantationsmedizin.html
- zusatz-weiterbildung-tropenmedizin.html

Since all 119 HTML files have matching JSON files, they were all converted at some
point -- the scripts were likely re-run with expanded file lists during development.

## Step 3: per-file JSON -> catalog.json (merge)

**Script:** `../dx-agents/target_schema.py to-json`
**Input:** `gebiete/*.json` (119 files)
**Output:** `catalog.json`
**Dependencies:** `pydantic`, `click`, `python-slugify`, `tqdm`

Reads all per-specialty JSON files, validates them against Pydantic models, and
merges them into a single `Catalog` structure with four top-level arrays:
- `medical_fields` (35) -- deduplicated from gebiet names
- `specialties` (52)
- `sub_specialties` (19)
- `additional_qualifications` (48)

Competency items are typed into `knowledge`, `skill`, or `course` based on which
column they appeared in. Items from the same `abschnitt` are grouped into
`CompetencySection` objects.

## Step 4: catalog.json -> catalog.duckdb (normalize)

**Script:** `../dx-agents/target_schema.py to-duckdb`
**Input:** `catalog.json`
**Output:** `catalog.duckdb`
**Dependencies:** `duckdb`, `pydantic`, `click`

Loads the catalog JSON, creates 6 normalized tables, and inserts all data.
Competency sections and items are flattened with auto-increment IDs and
polymorphic `owner_type`/`owner_id` references.

## Gaps and Issues

### Structural
1. **Scripts scattered across two repos** -- `split_mwbo.py`, `parse_8_gebiete.py`,
   and `target_schema.py` live in `../dx-agents/`, not in this repo.
2. **No single entry point** -- no Makefile, shell script, or CLI that runs the
   full pipeline end-to-end.
3. **Hardcoded absolute paths** -- all scripts use `/home/tom/Projects/health_bot/gebiete`
   (an old path) instead of relative paths.

### Step 2 (HTML -> JSON)
4. **8 scripts doing the same job** -- the HTML→JSON conversion was done
   incrementally with copy-paste-modify iterations. Each script handles a different
   subset of files and has slightly different parsing logic.
5. **No coverage guarantee** -- there is no manifest or check that confirms all 119
   HTML files were processed, and ~22 files are not in any script's explicit list.
6. **Mix of regex and BeautifulSoup** -- some scripts use raw regex, others use
   BeautifulSoup. The regex parsers are fragile against HTML formatting variations.
7. **No validation after parse** -- individual JSON files are not validated against
   the Pydantic schema (`gebiete.py`) until step 3.

### Step 3+4 (merge + load)
8. **Two-step merge** -- `to-json` and `to-duckdb` are separate commands. The JSON
   intermediate could be skipped if going straight to DuckDB.
9. **`gebiete.py` models vs `target_schema.py` models** -- two separate Pydantic
   model sets exist for the same data, with slightly different field names and
   structure. `gebiete.py` uses German field names; `target_schema.py` uses English.

## Refactoring Plan

Goal: a single reproducible pipeline `docx -> duckdb` that can be re-run when the
MWBO PDF is updated.

```
python pipeline.py run --input mwbo.docx --output catalog.duckdb
```

### Proposed steps

1. **Consolidate all scripts into this repo** -- move `split_mwbo.py` and
   `target_schema.py` into `mwbo_scripts/` (or a new `src/` directory).

2. **Unify HTML→JSON into one script** -- merge the 8 parsing scripts into a single
   `parse_html.py` that handles all three types (facharzt, schwerpunkt,
   zusatz-weiterbildung). Use BeautifulSoup consistently. Process all `gebiete/*.html`
   files automatically (no hardcoded file lists).

3. **Validate early** -- validate each per-file JSON against the Pydantic schema
   immediately after parsing, not just at merge time. Fail fast on parse errors.

4. **Single CLI entry point** -- create `pipeline.py` with subcommands:
   - `split` -- docx -> HTML files
   - `parse` -- HTML files -> per-file JSON
   - `build` -- per-file JSON -> catalog.json + catalog.duckdb
   - `run` -- all of the above in sequence

5. **Use relative paths everywhere** -- no hardcoded absolute paths.

6. **Drop `gebiete.py`** -- unify on one set of Pydantic models (the ones in
   `target_schema.py` which are more complete).

7. **Add a sanity check step** -- after `split`, verify the expected number of
   sections. After `parse`, verify all HTML files have matching JSON. After `build`,
   print row counts per table.
