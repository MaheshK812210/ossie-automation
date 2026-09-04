# Ossie Semantic Model Builder

A [Streamlit](https://streamlit.io/) app that turns spreadsheets exported
from a data catalog into a validated **Apache Ossie** ("Open Semantic
Interchange") semantic model YAML file -- in two stages, with a built-in
YAML viewer/editor in between.

Spec reference: [apache/ossie/core-spec](https://github.com/apache/ossie/tree/main/core-spec)
(Ossie core metadata specification, version `0.2.0.dev0`).

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app starts on `http://localhost:47531` (configured in
`.streamlit/config.toml`). Click **Load sample data** in the sidebar to try
Stage 1 immediately with a bundled example data model -- no files required.

## The two-stage workflow

### Stage 1 -- generate the base YAML

Upload up to three files (only the first is required):

1. **Table & column metadata** (required)
2. **Metrics, field synonyms & custom extensions** (optional)
3. **Relationships** (optional)

Click **Generate base YAML**. The app parses the files, builds an
Ossie-compliant semantic model, validates it against the official
[`ossie-schema.json`](schema/ossie-schema.json), and shows it to you in an
**editable text area** right in the browser -- make any manual tweaks you
want, click **Apply edits**, and it re-validates and locks in your changes.
You can download the YAML at this point, or continue to Stage 2.

### Stage 2 -- enrich with AI context (optional, later)

Once a base YAML exists, you can upload a separate **AI context** file
(anytime -- immediately, or much later, in a different session). It never
overwrites what's already there: it **concatenates** new instructions and
appends new synonyms/examples onto whatever a table/field/model already
has, and **appends** any `Custom Extension` value as a brand-new
`custom_extensions` entry. This lets a business/domain reviewer layer on
context without clobbering what the base generation (or a previous
enrichment pass) already produced.

## What you upload

### 1. Table & column metadata (required)

One row per **column**. Extra columns in your file are ignored; only these
headers (case/spacing-insensitive) are used:

| Header | Meaning |
|---|---|
| `Name` | Table or view name |
| `Assest Type` | e.g. `Fact Table`, `Dimension Table`, `View` |
| `Column Title` | Column name |
| `Description` | Business-friendly description |
| `Description from source system` | Description as documented by the source system |
| `size` | Column length/precision (e.g. `200`, `18,2`) |
| `Technical Data Type` | Physical/DB data type (e.g. `VARCHAR2(200)`, `NUMBER(18,2)`) |
| `Column Position` | Ordinal position of the column in the table |
| `Is Primary Key` | `Y`/`N` |
| `Is nullable` | `Y`/`N` |
| `Contains PII` | `Y`/`N` |
| `Primary Key` | Name/label of the primary key constraint (supports composite keys: give every column in a composite key the same label) |

### 2. Metrics, field synonyms & custom extensions (optional, app-defined format)

One sheet, one row per item, discriminated by a **`Type`** column:

| Header | Used by | Meaning |
|---|---|---|
| `Type` | all | `Metric`, `Synonym`, or `Custom Extension` |
| `Table Name` | Synonym, Custom Extension | Table the row applies to |
| `Column Name` | Synonym, Custom Extension | Column the row applies to (leave blank on a Custom Extension row for a **table-level** extension) |
| `Metric Name` | Metric | Unique metric identifier |
| `Metric Expression` | Metric | Aggregate SQL expression, e.g. `SUM(FACT_POSITION.MARKET_VALUE)` |
| `Metric Description` | Metric | What the metric measures |
| `Metric Data Type` | Metric | `String`/`Integer`/`Decimal`/`Float`/`Boolean`/`Date`/`Time`/`DateTime`/`DateTimeTz`/`Opaque` |
| `Synonyms` | Synonym | Comma-separated alternate names for that field |
| `Custom Extension Vendor` | Custom Extension | Free-form vendor name (defaults to `COMMON`) |
| `Custom Extension Data` | Custom Extension | A JSON object (used as-is) or free text (wrapped as `{"note": "..."}`) |

Metrics become native Ossie `metrics[]` entries at the model level.
Synonyms become the field's *base* `ai_context.synonyms`. Custom Extension
rows are placeholders you can use for anything vendor-specific up front
(masking policies, clustering keys, etc.) without waiting for the AI
enrichment stage.

### 3. Relationships (optional, app-defined format)

One row per foreign-key relationship between two tables:
`Relationship Name`, `From Table`, `From Columns`, `To Table`,
`To Columns`, `Relationship Type` (e.g. Many-to-One), `Description`.
`From Columns`/`To Columns` accept comma-separated lists for composite keys.

### 4. AI context enrichment (optional, uploaded later, app-defined format)

One row per table or column: `Table Name`, `Column Name` (leave blank for
table-level context, or use the literal value `MODEL` in `Table Name` for
model-level context), `Instructions`, `Synonyms`, `Examples`,
**`Custom Extension`**.

Unlike File 2's synonyms (which seed the *base* YAML), everything in this
file is **merged into an existing YAML**: `Instructions` are appended to
any existing instructions (separated by a blank line), `Synonyms` and
`Examples` are appended and de-duplicated, and `Custom Extension` becomes
a new `custom_extensions` entry with `vendor_name: AI_ENRICHMENT` --
nothing already in the YAML is overwritten or removed.

Ready-to-use example files for all four formats are in [`sample_data/`](sample_data/)
and can also be downloaded from the app's sidebar.

## Example data model: Account & Position

The bundled sample data represents a common wealth-management star schema:

- **`FACT_POSITION`** (fact) -- daily security holdings per account, with a
  composite grain of `ACCOUNT_ID` + `SECURITY_ID` + `AS_OF_DATE_ID`.
- **`DIM_ACCOUNT`** -- investment accounts (brokerage, IRA, 401k, trust, ...).
- **`DIM_CLIENT`** -- clients/households who own accounts.
- **`DIM_SECURITY`** -- tradable securities/instruments.
- **`DIM_DATE`** -- standard calendar date dimension.

Relationships connect the fact to each dimension, and `DIM_ACCOUNT` to
`DIM_CLIENT`. This is exactly what loads when you click **Load sample data**
(Stage 1) and **Use sample AI context file** (Stage 2).

## How fields map onto the Ossie spec

Ossie's `Dataset` and `Field` objects only accept a fixed set of properties
(the JSON Schema sets `additionalProperties: false`), so any source-system
attribute that isn't part of the core spec is preserved losslessly inside a
standard Ossie `custom_extensions` block instead of being dropped:

| Input | Ossie destination |
|---|---|
| `Name` | `datasets[].name` |
| `Column Title` | `datasets[].fields[].name` |
| `Description` | `datasets[].fields[].description` |
| `Technical Data Type` | best-effort mapped to `datasets[].fields[].datatype` enum (`String`, `Integer`, `Decimal`, `Float`, `Boolean`, `Date`, `Time`, `DateTime`, `DateTimeTz`, `Opaque`) **and** kept verbatim in `custom_extensions` |
| `size`, `Column Position`, `Is nullable`, `Contains PII`, `Description from source system` | `datasets[].fields[].custom_extensions` (`vendor_name: COMMON`) |
| `Is Primary Key` + `Primary Key` label | `datasets[].primary_key` (grouped into a single composite key per table) |
| `Assest Type`, PII column roll-up | `datasets[].custom_extensions` (`vendor_name: COMMON`) |
| File 2 `Metric` rows | native `semantic_model[].metrics[]` |
| File 2 `Synonym` rows | `datasets[].fields[].ai_context.synonyms` (base synonyms) |
| File 2 `Custom Extension` rows | `datasets[].custom_extensions` (table-level) or `datasets[].fields[].custom_extensions` (field-level) |
| Relationships file | native `semantic_model[].relationships[]` |
| File 4 AI context (Stage 2) | concatenated into `ai_context` + appended into `custom_extensions` (`vendor_name: AI_ENRICHMENT`) at model / dataset / field level |

The generated YAML is validated against the official Ossie JSON Schema
(`schema/ossie-schema.json`, fetched from the
[`apache/ossie`](https://github.com/apache/ossie) `core-spec/` directory)
every time it changes, and the app reports any validation errors inline.

## Project layout

```
app.py                          Streamlit UI (two-stage workflow)
ossie_builder.py                Parsing + YAML generation/merge logic (framework-free, unit-tested)
schema/ossie-schema.json         Official Apache Ossie JSON Schema (bundled for validation)
sample_data/                     Example input files (Account/Position data model)
scripts/generate_sample_data.py  Regenerates the sample_data/ files
tests/test_ossie_builder.py      Pytest suite: base generation + AI-context enrichment merge behavior
.streamlit/config.toml           Dev server port/config
```

## Running the tests

```bash
pip install -r requirements.txt pytest
pytest
```

## Notes & limitations

- Uploaded files never leave your machine/session -- everything is parsed
  and generated in-memory by the running Streamlit process.
- `Technical Data Type` mapping to the Ossie `datatype` enum is heuristic
  (regex-based on common SQL type names). Review the generated YAML for
  unusual/vendor-specific types, which fall back to `Opaque`.
- Re-running AI-context enrichment on the same file will append its
  content again (it's a simple additive merge) -- avoid re-uploading the
  same enrichment file twice unless you intend to duplicate its synonyms
  and appended `custom_extensions`.
