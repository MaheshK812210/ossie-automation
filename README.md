# Ossie Semantic Model Builder

A [Streamlit](https://streamlit.io/) app that turns spreadsheets exported
from a data catalog into a validated **Apache Ossie** ("Open Semantic
Interchange") semantic model YAML file, with a built-in YAML viewer/editor,
and can then export that model straight into a **Power BI semantic model**
-- no desktop application required for any of it.

Spec reference: [apache/ossie/core-spec](https://github.com/apache/ossie/tree/main/core-spec)
(Ossie core metadata specification, version `0.2.0.dev0`).

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app starts on `http://localhost:47531` (configured in
`.streamlit/config.toml`). Click **Load sample data** in the sidebar to try
it immediately with a bundled example data model -- no files required.

## Layout

- **Sidebar** (collapsible with Streamlit's own arrow): **⚙️ Model
  settings** (name/description) at the top, with a small **🌙** dark/light
  toggle next to its header, then a **section** selector (one button per
  section, the active one highlighted) -- **Base Model**, **Enrich Base
  Model**, **BI Conversions**, **AI Agent Invocation** (placeholder) --
  plus sample data, template downloads, and reset. Everything you
  configure or navigate with lives here in one place, and you can
  collapse it any time to reclaim screen width.
- Streamlit's default top toolbar (hamburger menu / "Deploy" button) is
  hidden -- it isn't useful for this app and just took up space.
- Each section shows a small colored badge above its header for quick
  visual identity: **BASE MODEL** (blue), **ENRICH BASE MODEL** (violet),
  **BI CONVERSIONS** (orange), **AI AGENT INVOCATION** (gray).
- **Dark / light theme**: toggle any time without losing your place --
  the current model, generated YAML, and selected section all persist
  across the switch. This is a thorough CSS re-theme (backgrounds, cards,
  text, form controls, buttons) rather than Streamlit's native theme
  system (which is fixed at server start), so it can be toggled instantly
  per-session.
- **Center column**: renders whichever section is currently selected in
  the sidebar.
- **Right column ("Ossie")**: a persistent, always-visible YAML view/edit
  panel shared by every section -- whatever the Base Model section
  generates, or the Enrich section enriches, shows up here immediately.
  Click **Full screen »** to expand it to (almost) the full page width
  (hiding the center column); **« Exit full screen** restores the normal
  view. Nothing is lost when toggling or switching sections -- the
  current model and YAML text persist throughout.

There is **no separate "database.schema" or "dialect" setting** anywhere
in the UI: each dataset's `source` is inferred entirely from the metadata
file's `Name` column (optionally qualified, e.g.
`WEALTH_DB.PUBLIC.FACT_POSITION`), and each metric's SQL dialect comes from
its own `Dialect` column in the metrics file (defaulting to `ANSI_SQL`).

### Base Model -- generate the base YAML

Upload up to three files (only the first is required):

1. **Table & column metadata** (required)
2. **Metrics, field synonyms & custom extensions** (optional)
3. **Relationships** (optional)

Click **Generate base YAML**. The app parses the files, builds an
Ossie-compliant semantic model, validates it against the official
[`ossie-schema.json`](schema/ossie-schema.json), and shows it in the
**Ossie** panel on the right in an **editable text area** -- make any
manual tweaks you want, click **Apply edits**, and it re-validates and
locks in your changes. Download the YAML at this point, or continue to
the Enrich section.

### Enrich Base Model -- add AI context (optional, later)

Once a base YAML exists, you can upload a separate **AI context** file
(anytime -- immediately, or much later, in a different session). It never
overwrites what's already there: it **concatenates** new instructions and
appends new synonyms/examples onto whatever a table/field/model already
has, and **appends** any `Custom Extension` value as a brand-new
`custom_extensions` entry. This lets a business/domain reviewer layer on
context without clobbering what the base generation (or a previous
enrichment pass) already produced.

### BI Conversions -- export to a BI tool (optional)

Once a base YAML exists, this section lets you convert it with one click.
Two options are shown; only **Power BI** is implemented (Tableau appears
as a clearly-labeled "not built yet" option):

- **Power BI**: generates a real **TMSL** (`model.bim`) document and a
  **TMDL**-based Power BI Project folder (the same text-based format behind
  modern `.pbip` projects and Fabric's git-integrated semantic models).
  - Download it as a ready-to-use `.zip` -- open the folder directly in
    Power BI Desktop via *File \u2192 Open \u2192 Power BI Project*, commit it to
    git for Fabric's git integration, or deploy it headlessly with the
    Tabular Editor CLI / Fabric REST API. None of that requires generating
    it from a desktop app.
  - Ossie `datasets`/`fields` become Tabular `tables`/`columns`; Ossie
    `relationships` become Tabular relationships (many-to-one, matching
    Ossie's own semantics); Ossie `metrics` become DAX `measures` via a
    best-effort ANSI SQL \u2192 DAX translator (`table.column` \u2192
    `table[column]`, `COUNT(DISTINCT x)` \u2192 `DISTINCTCOUNT(x)`, `AVG` \u2192
    `AVERAGE`, and a single top-level `a / b` \u2192 the zero-safe `DIVIDE(a, b)`).
  - Since this sandboxed environment has no live Power BI/Fabric workspace
    or connected data warehouse, there's a **"Run metrics against synthetic
    sample data"** button: it generates small, randomly-typed sample
    dataframes matching your schema and runs each metric's original SQL
    expression against them with an in-memory DuckDB engine, just to prove
    the metric logic (and its DAX translation) executes correctly
    end-to-end. These are illustrative values, not real business results.
  - **Connecting to real Snowflake tables**: check **"Use Snowflake as the
    data source"** and fill in your Snowflake **Account URL** (e.g.
    `myorg-myaccount.snowflakecomputing.com`), **Warehouse**, and
    optionally a **Role**, then convert. This generates real
    `Snowflake.Databases(...)` Power Query (M) code -- Power BI's native
    Snowflake connector syntax -- instead of the generic placeholder.
    Database/schema/table names come from each dataset's Ossie `source`
    field, which is inferred from the metadata file's qualified `Name`
    column (e.g. `MY_DB.PUBLIC.MY_TABLE` -- see "Table & column metadata"
    below). **No credentials are entered or stored anywhere in the app or
    the exported files** -- open the downloaded `.SemanticModel` folder as
    a Power BI Project in Power BI Desktop and hit Refresh; Power BI's
    Snowflake connector will prompt you for sign-in (username/password,
    SSO, or key-pair) at that point, exactly as it would for any other
    Snowflake-backed report.
  - **Two ways to get the model into Power BI/Fabric** -- both are kept
    available side by side:
    1. **Power BI Desktop** (`⬇️ Download Power BI Project (.zip)`):
       unzip it and open the `.SemanticModel` folder via *File \u2192 Open
       \u2192 Power BI Project*. Familiar, works everywhere Desktop runs.
    2. **Deploy to Fabric** (the `\U0001f6f0\ufe0f Deploy to Fabric` sub-tab,
       fully headless, no desktop app at all): calls the real
       [Fabric REST API](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model)
       (`POST /v1/workspaces/{id}/semanticModels`) to create the semantic
       model item directly in a Fabric workspace over plain HTTPS. Fill in
       a **Fabric workspace ID (GUID)** and a **bearer token** (get one
       from any terminal with `az login` then
       `az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv`
       -- no credentials are stored by the app, only used for that one
       request) and click **Deploy**. The app handles the API's
       long-running-operation/polling pattern and reports success or a
       real, readable error.
       - **Requires a Fabric-enabled workspace** (Fabric trial capacity,
         Premium, or Premium Per User -- plain free/Pro workspaces don't
         support this API; that's a Microsoft licensing restriction, not
         something this app can route around). If your organization
         doesn't have one: check whether a 60-day Fabric trial is
         available (Power BI/Fabric admin portal -- only works on
         established tenants, not brand-new ones), or spin up a
         pay-as-you-go F2 capacity in Azure (~$0.35/hour, pausable) purely
         for deployment.
       - **No Fabric workspace or token handy?** Commit the downloaded
         `.SemanticModel` folder to a git repo connected to a Fabric
         workspace's **git integration** instead -- `git push` alone syncs
         it, with no API call and no desktop app either.

### AI Agent Invocation (placeholder)

A placeholder section for a future AI agent integration (e.g. answering
natural-language questions using the current model's metrics,
relationships, and `ai_context`). It shows a disabled input/button once a
base YAML exists; the actual agent invocation code is not implemented yet.

## Model registry (save/load to a git repo)

The **Ossie** panel (right column) has a **📚 Registry** section below
"Apply edits"/"Download YAML", visible in the **Base Model** and **Enrich
Base Model** sections only:

- **Base Model** saves/loads `basemodel/<model name>.ossie.yaml`
- **Enrich Base Model** saves/loads `AIEnrich/<model name>.ossie.yaml`

Both live in a separate GitHub repository
([`MaheshK812210/ModelRegitry`](https://github.com/MaheshK812210/ModelRegitry)
by default, overridable -- see below) via the GitHub Contents API
(`git_registry.py`) -- no local git clone, no desktop application.

- **Load works even before you've generated a model**: the Registry
  section (and its **📂 Load from ...** picker) is shown as soon as you
  pick the Base Model or Enrich Base Model section, whether or not a
  model exists yet -- you don't need to run **Generate base YAML** first
  just to open and edit a model that's already saved. **Save**, on the
  other hand, only appears once a model exists (there'd be nothing to
  save otherwise).
- **Filename**: derived from the sidebar's **Model name** field (one file
  per model name per directory).
- **Save** always **overwrites** that file ("last write wins" -- whichever
  save reaches GitHub last simply replaces the content, with no conflict
  check shown to you). Versioning comes from the registry repo's own git
  history on that file, not from separate timestamped files; the optional
  "Save notes" field becomes the commit message.
- **Load** shows a dropdown of every model currently saved in that
  section's directory; pick one and click **Load selected model** to pull
  it into the YAML panel (you can then edit and save it back).
- **Loading updates the sidebar's Model name field too** -- it's set to the
  loaded model's own `name`, so a subsequent **Save** by default overwrites
  the same file you just loaded rather than a stale name you'd typed
  earlier.
- **Editing after a load is always possible**: you can edit the YAML
  directly in the panel, or go back to **Base Model** and upload just a
  new/updated piece -- see "Incremental updates" below -- table metadata
  isn't required again just to add a metric or a relationship.

### Incremental updates once a model is loaded

Once a model exists in the Ossie panel -- whether freshly generated,
loaded from the registry, or hand-edited -- the **Base Model** section's
three uploads (table/column metadata, metrics/synonyms/extensions,
relationships) all become **optional**, and the button changes from
**🚀 Generate base YAML** to **🔄 Apply changes to loaded model**. Upload
just the piece you want to change:

- **Only metrics/synonyms/extensions**: new metrics are added (a metric
  with the same `name` as an existing one replaces it), field synonyms are
  added to whatever synonyms a field already has, and custom extensions
  are appended -- nothing about the existing tables changes.
- **Only relationships**: new relationships are added the same way
  (upserted by name); a relationship referencing a table that doesn't
  exist in the model is skipped with a warning, same as during the
  original generation.
- **Only table metadata**: any table name in the file that already exists
  in the model is fully replaced with the new definition (mirrors
  re-uploading that table); an unseen table name is added as a new
  dataset. Existing metrics/relationships/synonyms on tables you didn't
  re-upload are left untouched.
- **Any combination** of the three at once works the same way, all applied
  together.

If nothing is uploaded, the section just shows a summary of what's
currently loaded (dataset/field/relationship/metric counts) -- you can
still edit the YAML directly on the right at any time.

### Configuring the registry token

Copy `.env.example` to `.env` and set:

```bash
GIT_REGISTRY_TOKEN=ghp_...          # GitHub PAT with write access to the registry repo
GIT_REGISTRY_OWNER=MaheshK812210    # optional, defaults shown
GIT_REGISTRY_REPO=ModelRegitry
GIT_REGISTRY_BRANCH=main
```

`.env` is git-ignored -- it's never committed. Without a token configured,
the Registry section shows a clear "not configured" message instead of
erroring; every other feature in the app works fine without it.

## What you upload

### 1. Table & column metadata (required)

One row per **column**. Extra columns in your file are ignored; only these
headers (case/spacing-insensitive) are used:

| Header | Meaning |
|---|---|
| `Name` | Table or view name -- **optionally qualified** with `database.schema.`, e.g. `WEALTH_DB.PUBLIC.FACT_POSITION`. There is no separate database/schema setting anywhere else in the app: the last dot-separated segment becomes the Ossie dataset name (and is what relationships/metrics/synonyms/relationships files reference), while the full string becomes the dataset's `source` field verbatim. A plain unqualified name (e.g. just `FACT_POSITION`) works too -- `source` then equals the table name. |
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
| `Dialect` | Metric | SQL dialect of `Metric Expression`: one of `ANSI_SQL`, `SNOWFLAKE`, `MDX`, `TABLEAU`, `DATABRICKS`, `MAQL`, `BIGQUERY`, `THOUGHTSPOT`. Optional; defaults to (and falls back on any unrecognized value to) `ANSI_SQL`, with a warning. This is the **only** place a dialect is set anywhere in the app -- it's per-metric, not global. Field expressions are always `ANSI_SQL` (they're just plain column references). |
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
(sidebar) and **Use sample AI context file** (Enrich Base Model section).
The sample metadata file qualifies every table as `WEALTH_DB.PUBLIC.<table>`
to demonstrate the `source`-inference behavior described above.

## How fields map onto the Ossie spec

Ossie's `Dataset` and `Field` objects only accept a fixed set of properties
(the JSON Schema sets `additionalProperties: false`), so any source-system
attribute that isn't part of the core spec is preserved losslessly inside a
standard Ossie `custom_extensions` block instead of being dropped:

| Input | Ossie destination |
|---|---|
| `Name` | `datasets[].name` (short/last segment) + `datasets[].source` (full qualified string, verbatim) |
| `Column Title` | `datasets[].fields[].name` |
| `Description` | `datasets[].fields[].description` |
| `Technical Data Type` | best-effort mapped to `datasets[].fields[].datatype` enum (`String`, `Integer`, `Decimal`, `Float`, `Boolean`, `Date`, `Time`, `DateTime`, `DateTimeTz`, `Opaque`) **and** kept verbatim in `custom_extensions` |
| `size`, `Column Position`, `Is nullable`, `Contains PII`, `Description from source system` | `datasets[].fields[].custom_extensions` (`vendor_name: COMMON`) |
| `Is Primary Key` + `Primary Key` label | `datasets[].primary_key` (grouped into a single composite key per table) |
| `Assest Type`, PII column roll-up | `datasets[].custom_extensions` (`vendor_name: COMMON`) |
| File 2 `Metric` rows (+ their own `Dialect`) | native `semantic_model[].metrics[]`, each with its own `expression.dialects[].dialect` |
| File 2 `Synonym` rows | `datasets[].fields[].ai_context.synonyms` (base synonyms) |
| File 2 `Custom Extension` rows | `datasets[].custom_extensions` (table-level) or `datasets[].fields[].custom_extensions` (field-level) |
| Relationships file | native `semantic_model[].relationships[]` |
| File 4 AI context (Enrich Base Model) | concatenated into `ai_context` + appended into `custom_extensions` (`vendor_name: AI_ENRICHMENT`) at model / dataset / field level |

The generated YAML is validated against the official Ossie JSON Schema
(`schema/ossie-schema.json`, fetched from the
[`apache/ossie`](https://github.com/apache/ossie) `core-spec/` directory)
every time it changes, and the app reports any validation errors inline.

## Project layout

```
app.py                            Streamlit UI (sidebar settings/nav, center = active section, right = Ossie YAML)
ossie_builder.py                  Ossie parsing + YAML generation/merge logic (framework-free, unit-tested)
powerbi_export.py                 Ossie -> Power BI (TMSL/TMDL) conversion + synthetic-data metric preview
fabric_deploy.py                  Headless Fabric REST API deployment (no Power BI Desktop required)
git_registry.py                   Save/load models to a GitHub-backed registry repo (GitHub Contents API)
schema/ossie-schema.json           Official Apache Ossie JSON Schema (bundled for validation)
sample_data/                       Example input files (Account/Position data model)
scripts/generate_sample_data.py    Regenerates the sample_data/ files
tests/test_ossie_builder.py        Pytest suite: base generation + AI-context enrichment merge behavior
tests/test_powerbi_export.py       Pytest suite: SQL->DAX translation, TMSL/TMDL output, metric preview
tests/test_fabric_deploy.py        Pytest suite: Fabric API payload construction + mocked deploy/poll flows
tests/test_git_registry.py         Pytest suite: registry list/load/save (create vs. overwrite) + error paths
.env.example                       Template for GIT_REGISTRY_TOKEN and related settings (copy to .env)
.streamlit/config.toml             Dev server port/config + color theme
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
- The Power BI export is a best-effort structural conversion (SQL -> DAX
  translation, Tabular relationship mapping). Composite relationship keys
  are reduced to their first column pair (Tabular relationships are
  single-column), and the placeholder Power Query (M) source expressions
  need to be pointed at a real data source before deploying for actual use.
  Review the generated `model.bim`/TMDL before deploying to production.
- The "Tables & columns" tree (Base Model section, step 2) is a real
  folder/checkbox tree (via [`streamlit-tree-select`](https://pypi.org/project/streamlit-tree-select/)):
  tables are folders, columns are leaves, and a table's checkbox shows a
  tri-state (checked/unchecked/indeterminate) reflecting its columns.
  Tables are collapsed by default to keep the list compact; use its
  "Expand all" control to review everything at once. This component
  renders in an iframe, which browsers don't let CSS reliably scroll
  independently of the page -- expanding many tables at once makes the
  whole page taller/scrollable rather than showing its own scrollbar.
