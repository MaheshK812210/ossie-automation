# Ossie Semantic Model Builder

A [Streamlit](https://streamlit.io/) app that turns spreadsheets exported
from a data catalog into a validated **Apache Ossie** ("Open Semantic
Interchange") semantic model YAML file.

Upload table/column metadata, snapshot details, table relationships, and
AI-context notes as CSV, XLSX, or TXT files straight from your browser --
the app parses them, builds an Ossie-compliant semantic model, validates it
against the official [`ossie-schema.json`](schema/ossie-schema.json), and
lets you download the generated YAML.

Spec reference: [apache/ossie/core-spec](https://github.com/apache/ossie/tree/main/core-spec)
(Ossie core metadata specification, version `0.2.0.dev0`).

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app starts on `http://localhost:47531` (configured in
`.streamlit/config.toml`). Click **Load sample data** in the sidebar to try
the app immediately with a bundled example data model -- no files required.

## What you upload

The app expects up to four separate files. Only the first is required; the
rest are optional and simply enrich the generated model.

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

### 2. Snapshot details (optional, app-defined format)

One row per table, describing how/when it's captured:
`Table Name`, `Snapshot Type` (Full/Incremental), `Snapshot Frequency`,
`Snapshot Date Column`, `Partition Column`, `History Type` (e.g. SCD
Type 1/2), `Retention Period`, `Source System`, `Load Pattern`.

### 3. Relationships (optional, app-defined format)

One row per foreign-key relationship between two tables:
`Relationship Name`, `From Table`, `From Columns`, `To Table`,
`To Columns`, `Relationship Type` (e.g. Many-to-One), `Description`.
`From Columns`/`To Columns` accept comma-separated lists for composite keys.

### 4. AI context / instructions (optional, app-defined format)

One row per table or column, feeding Ossie's native `ai_context` blocks:
`Table Name`, `Column Name` (leave blank for table-level context, or use the
literal value `MODEL` in `Table Name` for model-level context), `Instructions`,
`Synonyms`, `Examples`.

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
`DIM_CLIENT`. This is exactly what loads when you click **Load sample data**.

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
| Snapshot file | `datasets[].custom_extensions` (`vendor_name: SNAPSHOT`) |
| Relationships file | native `semantic_model[].relationships[]` |
| AI context file | native `ai_context` at model / dataset / field level |

The generated YAML is validated against the official Ossie JSON Schema
(`schema/ossie-schema.json`, fetched from the
[`apache/ossie`](https://github.com/apache/ossie) `core-spec/` directory)
before it's shown to you, and the app reports any validation errors inline.

## Project layout

```
app.py                          Streamlit UI
ossie_builder.py                Parsing + YAML generation logic (framework-free, unit-tested)
schema/ossie-schema.json         Official Apache Ossie JSON Schema (bundled for validation)
sample_data/                     Example input files (Account/Position data model)
scripts/generate_sample_data.py  Regenerates the sample_data/ files
tests/test_ossie_builder.py      Pytest suite, including full-pipeline schema validation
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
- The app currently focuses on `datasets`, `relationships`, and `ai_context`.
  Ossie also supports model-level `metrics`; extending the metadata format
  to author metrics would be a natural follow-up.
