# mwbo - German Medical Specialties (MWBO), machine-readable

The [Muster-Weiterbildungsordnung (MWBO)](https://www.bundesaerztekammer.de/themen/aerzte/aus-fort-und-weiterbildung/aerztliche-weiterbildung/muster-weiterbildungsordnung) is the official model regulation by the German Medical Association (Bundesärztekammer) that defines all recognized medical specialties, their grouping into fields, and the competencies required during specialist training. It is the authoritative source for what German physicians must learn to become board-certified specialists.

Unfortunately, the MWBO is only published as a PDF. This project makes it machine-readable.

## Pipeline

```
mwbo.docx  (Adobe PDF-to-Word from the Bundesärztekammer PDF)
  │  pipeline.py run
  ▼
catalog.duckdb   (normalized relational database)
catalog.json     (optional, --json flag)
catalog.html     (optional, --html flag)
```

### Usage

```bash
python -m venv .venv
.venv/bin/pip install beautifulsoup4 click duckdb 'pydantic>=2' python-slugify

# Full pipeline
.venv/bin/python pipeline.py run -o catalog.duckdb

# With JSON and HTML exports
.venv/bin/python pipeline.py run -o catalog.duckdb --json catalog.json --html catalog.html

# Export from existing DuckDB
.venv/bin/python pipeline.py export -i catalog.duckdb --json catalog.json --html catalog.html

# Individual steps
.venv/bin/python pipeline.py split
.venv/bin/python pipeline.py parse
.venv/bin/python pipeline.py build

# Compare two databases
.venv/bin/python pipeline.py compare old/catalog.duckdb catalog.duckdb
```

Requires `pandoc` to be installed for the docx-to-HTML conversion.

## Source files

| File | Description |
|---|---|
| [20250703_MWBO-2018.pdf](./20250703_MWBO-2018.pdf) | Original PDF from [Bundesärztekammer](https://www.bundesaerztekammer.de/fileadmin/user_upload/BAEK/Themen/Aus-Fort-Weiterbildung/Weiterbildung/20250703_MWBO-2018.pdf) |
| [mwbo.docx](./mwbo.docx) | Converted via [Adobe PDF-to-Word](https://www.adobe.com/acrobat/online/pdf-to-word.html) |
| [pipeline.py](./pipeline.py) | Unified pipeline: docx -> HTML -> JSON -> DuckDB |
| [old/](./old/) | Archived original scripts (see [old/README.md](./old/README.md)) |

## Database Schema

### `medical_fields` — 34 rows
Top-level grouping of medicine (Gebiete), e.g. "Innere Medizin", "Chirurgie", "Augenheilkunde".

| Column | Type | Description |
|---|---|---|
| `id` | VARCHAR, PK | Slug identifier, e.g. `innere_medizin` |
| `name` | VARCHAR | Display name, e.g. "Innere Medizin" |

### `specialties` — 52 rows
Board-certified specializations (Facharztbezeichnungen) within a medical field, e.g. "Facharzt/Fachärztin für Kardiologie".

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER, PK | Auto-increment ID |
| `name` | VARCHAR | Full title |
| `medical_field_id` | VARCHAR, FK → medical_fields | Parent medical field |
| `alternative_title` | VARCHAR, nullable | Alternative designation |
| `field_definition` | VARCHAR | Official scope definition |
| `training_duration` | VARCHAR | Required training duration and structure |

### `sub_specialties` — 19 rows
Subspecialties / focus areas (Schwerpunktbezeichnungen) within a specialty, e.g. "Schwerpunkt Spezielle Unfallchirurgie".

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER, PK | Auto-increment ID |
| `name` | VARCHAR | Full title |
| `parent_specialty` | VARCHAR | Name of the parent specialty |
| `alternative_title` | VARCHAR, nullable | Alternative designation |
| `prerequisite` | VARCHAR | Required prior qualification |
| `training_duration` | VARCHAR | Additional training required |

### `additional_qualifications` — 48 rows
Additional qualifications (Zusatz-Weiterbildungen) that can be acquired on top of a specialty, e.g. "Allergologie", "Intensivmedizin", "Palliativmedizin".

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER, PK | Auto-increment ID |
| `name` | VARCHAR | Full title |
| `definition` | VARCHAR | Official scope definition |
| `requirements` | VARCHAR | Prerequisites and training requirements |

### `competency_sections` — 1,113 rows
Thematic sections grouping the competency items for each specialty, sub-specialty, or additional qualification. E.g. "Notfälle", "Krankheiten und Beratungsanlässe".

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER, PK | Auto-increment ID |
| `owner_type` | VARCHAR | One of: `specialty`, `sub_specialty`, `additional_qualification` |
| `owner_id` | INTEGER | FK to the respective owner table |
| `name` | VARCHAR | Section heading |

### `competency_items` — 6,913 rows
Individual competency requirements (Weiterbildungsinhalte). These are the specific things a physician must know or be able to do. Items can be nested via `parent_item_id`.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER, PK | Auto-increment ID |
| `section_id` | INTEGER, FK → competency_sections | Parent section |
| `parent_item_id` | INTEGER, nullable | Self-referencing FK for nested items |
| `type` | VARCHAR | One of: `knowledge` (Kenntnisse), `skill` (Erfahrungen und Fertigkeiten), `course` (Kurs-Weiterbildung) |
| `description` | VARCHAR | What must be learned or demonstrated |
| `target_number` | INTEGER, nullable | Required minimum count (Richtzahl), e.g. 50 house calls |

## Example Queries

```sql
-- List all medical fields with their specialty count
SELECT mf.name, COUNT(s.id) as specialties
FROM medical_fields mf
LEFT JOIN specialties s ON s.medical_field_id = mf.id
GROUP BY mf.name ORDER BY specialties DESC;

-- Get the training profile for a specific specialty
SELECT cs.name as section, ci.type, ci.description, ci.target_number
FROM specialties s
JOIN competency_sections cs ON cs.owner_type = 'specialty' AND cs.owner_id = s.id
JOIN competency_items ci ON ci.section_id = cs.id
WHERE s.name LIKE '%Allgemeinmedizin%'
ORDER BY cs.id, ci.id;
```

