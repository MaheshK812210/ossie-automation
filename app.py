"""Streamlit app: a two-stage Apache Ossie (Open Semantic Interchange)
semantic model builder.

Stage 1 (base generation): upload table/column metadata (required) plus
optional metrics / field-synonyms / custom_extensions and relationships
files, generate a base Ossie YAML, and view/edit it directly in the
browser.

Stage 2 (AI-context enrichment, optional, later): upload a separate
AI-context file that concatenates additional instructions/synonyms/
examples and appends custom_extensions onto the (possibly hand-edited)
base YAML.

Spec reference: https://github.com/apache/ossie/tree/main/core-spec
"""

import os
import traceback

import streamlit as st
import yaml

import ossie_builder as ob

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT_DIR, "schema", "ossie-schema.json")
SAMPLE_DIR = os.path.join(ROOT_DIR, "sample_data")

SAMPLE_FILES = {
    "metadata": "01_table_column_metadata.csv",
    "enrichment": "02_metrics_synonyms_extensions.csv",
    "relationships": "03_relationships.csv",
    "ai_context": "04_ai_context.csv",
}

st.set_page_config(
    page_title="Ossie Semantic Model Builder",
    page_icon="🧬",
    layout="wide",
)


def _read_sample(key: str) -> bytes:
    path = os.path.join(SAMPLE_DIR, SAMPLE_FILES[key])
    with open(path, "rb") as f:
        return f.read()


def _load_df_from_upload(uploaded_file):
    if uploaded_file is None:
        return None
    raw = uploaded_file.getvalue()
    return ob.load_tabular_bytes(uploaded_file.name, raw)


def _load_df_from_sample(key: str):
    raw = _read_sample(key)
    return ob.load_tabular_bytes(SAMPLE_FILES[key], raw)


def _dataset_field_counts(model):
    entry = model["semantic_model"][0]
    n_datasets = len(entry.get("datasets", []))
    n_fields = sum(len(d.get("fields", [])) for d in entry.get("datasets", []))
    n_rels = len(entry.get("relationships", []))
    n_metrics = len(entry.get("metrics", []))
    return n_datasets, n_fields, n_rels, n_metrics


def _show_validation(model):
    errors = ob.validate_model(model, SCHEMA_PATH)
    if errors:
        st.error("This YAML does NOT pass validation against the Ossie JSON Schema:")
        for err in errors:
            st.code(err, language="text")
    else:
        st.success("✅ Valid against the Apache Ossie core-spec JSON Schema.")
    return errors


st.session_state.setdefault("use_sample_base", False)
st.session_state.setdefault("use_sample_ai_context", False)
st.session_state.setdefault("model", None)
st.session_state.setdefault("yaml_editor", "")
st.session_state.setdefault("enrichment_message", None)

# A widget's session_state value can only be set BEFORE that widget is
# instantiated in a given script run. Stage 2 (below) computes its result
# after the "yaml_editor" text_area has already been created, so it stashes
# the new model here and triggers a rerun; this block -- which runs before
# the text_area is (re)created -- is what actually applies it.
if st.session_state.get("_pending_model") is not None:
    st.session_state.model = st.session_state.pop("_pending_model")
    st.session_state.yaml_editor = ob.to_yaml(st.session_state.model)

# ---------------------------------------------------------------------------
# Sidebar: model settings + sample data + templates
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Semantic model settings")
    model_name = st.text_input("Model name", value="account_position_model")
    model_description = st.text_area(
        "Model description",
        value="Investment account and position semantic model covering client, "
        "account, security, and calendar dimensions with a daily position fact.",
        height=90,
    )
    dialect = st.selectbox("Default SQL dialect", ob.VALID_DIALECTS, index=0)
    source_prefix = st.text_input(
        "Source prefix (database.schema)",
        value="wealth.public",
        help="Prepended to each table name to build the dataset 'source' "
        "field, e.g. 'wealth.public' + 'FACT_POSITION' -> "
        "'wealth.public.FACT_POSITION'. Leave blank to use table names as-is.",
    )

    st.divider()
    st.header("Try it with sample data")
    st.caption(
        "Loads a ready-made **Account / Position** investment data model "
        "(1 fact table + 4 dimensions) so you can see the app end-to-end "
        "before uploading your own files."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Load sample data", use_container_width=True):
            st.session_state.use_sample_base = True
    with col_b:
        if st.button("Clear sample data", use_container_width=True):
            st.session_state.use_sample_base = False

    with st.expander("Download blank templates", expanded=False):
        for key, label in [
            ("metadata", "1. Table/Column metadata"),
            ("enrichment", "2. Metrics, synonyms & custom extensions"),
            ("relationships", "3. Relationships"),
            ("ai_context", "4. AI context enrichment"),
        ]:
            st.download_button(
                label=f"⬇️ {label} sample CSV",
                data=_read_sample(key),
                file_name=SAMPLE_FILES[key],
                mime="text/csv",
                use_container_width=True,
                key=f"dl_{key}",
            )

    st.divider()
    if st.button("🗑️ Start over (clear everything)", use_container_width=True):
        st.session_state.model = None
        st.session_state.yaml_editor = ""
        st.session_state.use_sample_base = False
        st.session_state.use_sample_ai_context = False
        st.rerun()

st.title("🧬 Apache Ossie Semantic Model Builder")
st.markdown(
    "**Stage 1:** upload table metadata, metrics/synonyms, and relationships to generate a base "
    "**Apache Ossie** ([core-spec](https://github.com/apache/ossie/tree/main/core-spec)) semantic "
    "model YAML -- then view and edit it right here. **Stage 2 (later, optional):** upload an "
    "AI-context file to enrich it further."
)

# ---------------------------------------------------------------------------
# Stage 1a: Upload base files
# ---------------------------------------------------------------------------

st.header("Stage 1 -- Generate the base YAML")
st.subheader("1. Upload your files")
st.caption("Accepted formats: **.csv, .xlsx, .txt** (delimited). Multi-sheet Excel workbooks are combined automatically.")

upload_cols = st.columns(3)

with upload_cols[0]:
    st.markdown("**① Table & column metadata** `required`")
    st.caption(
        "One row per column. Required headers: Name, Assest Type, Column "
        "Title, Description, Description from source system, size, "
        "Technical Data Type, Column Position, Is Primary Key, Is nullable, "
        "Contains PII, Primary Key."
    )
    metadata_upload = st.file_uploader(
        "Metadata file", type=["csv", "xlsx", "txt"], key="metadata_upload", label_visibility="collapsed"
    )

with upload_cols[1]:
    st.markdown("**② Metrics, field synonyms & custom extensions** `optional`")
    st.caption(
        "One row per item, discriminated by **Type** = `Metric` / `Synonym` / "
        "`Custom Extension`: Table Name, Column Name, Metric Name, Metric "
        "Expression, Metric Description, Metric Data Type, Synonyms, "
        "Custom Extension Vendor, Custom Extension Data."
    )
    enrichment_upload = st.file_uploader(
        "Metrics/synonyms/extensions file", type=["csv", "xlsx", "txt"],
        key="enrichment_upload", label_visibility="collapsed",
    )

with upload_cols[2]:
    st.markdown("**③ Relationships** `optional`")
    st.caption(
        "One row per FK relationship: Relationship Name, From Table, From "
        "Columns, To Table, To Columns, Relationship Type, Description."
    )
    relationships_upload = st.file_uploader(
        "Relationships file", type=["csv", "xlsx", "txt"], key="relationships_upload", label_visibility="collapsed"
    )

use_sample_base = st.session_state.use_sample_base
if use_sample_base:
    st.info(
        "Using the bundled **Account / Position** sample data for Stage 1. Click "
        "**Clear sample data** in the sidebar to switch back to your own uploads.",
        icon="ℹ️",
    )

try:
    metadata_df = _load_df_from_sample("metadata") if use_sample_base else _load_df_from_upload(metadata_upload)
    enrichment_df = _load_df_from_sample("enrichment") if use_sample_base else _load_df_from_upload(enrichment_upload)
    relationships_df = _load_df_from_sample("relationships") if use_sample_base else _load_df_from_upload(relationships_upload)
except Exception as e:  # noqa: BLE001
    st.error(f"Failed to read one of the uploaded files: {e}")
    st.stop()

if metadata_df is None:
    st.warning("Upload the table/column metadata file (or load the sample data) to continue.")
    st.stop()

st.subheader("2. Preview parsed input")
preview_tabs = st.tabs(["Metadata", "Metrics / synonyms / extensions", "Relationships"])
with preview_tabs[0]:
    st.dataframe(metadata_df, use_container_width=True, height=260)
with preview_tabs[1]:
    if enrichment_df is not None:
        st.dataframe(enrichment_df, use_container_width=True, height=220)
    else:
        st.caption("No metrics/synonyms/custom-extensions file uploaded.")
with preview_tabs[2]:
    if relationships_df is not None:
        st.dataframe(relationships_df, use_container_width=True, height=220)
    else:
        st.caption("No relationships file uploaded.")

# ---------------------------------------------------------------------------
# Stage 1b: Generate base model
# ---------------------------------------------------------------------------

st.subheader("3. Generate the base Ossie YAML")
generate = st.button("🚀 Generate base YAML", type="primary")

if generate:
    try:
        tables = ob.parse_metadata(metadata_df)
        enrichment = ob.parse_metrics_synonyms_extensions(enrichment_df)
        relationships = ob.parse_relationships(relationships_df)

        result = ob.build_semantic_model(
            model_name=model_name.strip() or "semantic_model",
            model_description=model_description.strip(),
            dialect=dialect,
            source_prefix=source_prefix.strip().rstrip("."),
            tables=tables,
            relationships=relationships,
            metrics=enrichment.metrics,
            field_synonyms=enrichment.field_synonyms,
            dataset_extensions=enrichment.dataset_extensions,
            field_extensions=enrichment.field_extensions,
        )

        for w in enrichment.warnings:
            st.warning(w)
        for w in result.warnings:
            st.warning(w)

        st.session_state.model = result.model
        st.session_state.yaml_editor = ob.to_yaml(result.model)
        st.toast("Base YAML generated.", icon="✅")

    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to generate the semantic model: {e}")
        with st.expander("Show details"):
            st.code(traceback.format_exc())

# ---------------------------------------------------------------------------
# View & edit the current YAML (available once a base model exists)
# ---------------------------------------------------------------------------

if st.session_state.model is not None:
    n_datasets, n_fields, n_rels, n_metrics = _dataset_field_counts(st.session_state.model)
    metric_cols = st.columns(4)
    metric_cols[0].metric("Datasets", n_datasets)
    metric_cols[1].metric("Fields", n_fields)
    metric_cols[2].metric("Relationships", n_rels)
    metric_cols[3].metric("Metrics", n_metrics)

    st.subheader("4. View & edit the YAML")
    st.caption(
        "Make any manual changes you like, then click **Apply edits** to "
        "re-validate and lock them in -- later AI-context enrichment (Stage "
        "2 below) builds on top of whatever is applied here."
    )
    st.text_area("Ossie YAML", key="yaml_editor", height=480, label_visibility="collapsed")

    edit_cols = st.columns([1, 1, 2])
    with edit_cols[0]:
        apply_edits = st.button("✅ Apply edits")
    with edit_cols[1]:
        st.download_button(
            label="⬇️ Download current YAML",
            data=st.session_state.yaml_editor,
            file_name=f"{(model_name.strip() or 'semantic_model')}.ossie.yaml",
            mime="application/x-yaml",
        )

    if apply_edits:
        try:
            parsed = ob.parse_yaml_text(st.session_state.yaml_editor)
            st.session_state.model = parsed
            st.success("Edits applied.")
        except (yaml.YAMLError, ValueError) as e:
            st.error(f"Could not parse your edits as valid YAML: {e}")

    _show_validation(st.session_state.model)

# ---------------------------------------------------------------------------
# Stage 2: AI-context enrichment (optional, later)
# ---------------------------------------------------------------------------

if st.session_state.model is not None:
    st.divider()
    st.header("Stage 2 -- Enrich with AI context (optional, later)")
    st.caption(
        "Upload this **after** you already have a base YAML (freshly generated or hand-edited "
        "above). It never overwrites existing instructions/synonyms/examples -- it **concatenates** "
        "new ones onto whatever a table/field/model already has, and **appends** any `Custom "
        "Extension` value as a new `custom_extensions` entry (`vendor_name: AI_ENRICHMENT`)."
    )
    st.caption(
        "One row per table or column: Table Name, Column Name (blank = table-level; `MODEL` = "
        "model-level), Instructions, Synonyms, Examples, **Custom Extension**."
    )

    ai_cols = st.columns([2, 1])
    with ai_cols[0]:
        ai_context_upload = st.file_uploader(
            "AI context file", type=["csv", "xlsx", "txt"], key="ai_context_upload"
        )
    with ai_cols[1]:
        st.write("")
        st.write("")
        if st.button("Use sample AI context file"):
            st.session_state.use_sample_ai_context = True

    use_sample_ai = st.session_state.use_sample_ai_context
    if use_sample_ai:
        st.info("Using the bundled sample AI-context file.", icon="ℹ️")

    try:
        ai_context_df = _load_df_from_sample("ai_context") if use_sample_ai else _load_df_from_upload(ai_context_upload)
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to read the AI context file: {e}")
        ai_context_df = None

    if ai_context_df is not None:
        st.dataframe(ai_context_df, use_container_width=True, height=220)

        if st.button("🧠 Enrich YAML with AI context", type="primary"):
            try:
                model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(ai_context_df)
                enriched = ob.merge_ai_context_into_model(
                    st.session_state.model, model_ctx, dataset_ctx, field_ctx
                )
                # Can't touch st.session_state.yaml_editor directly here --
                # that widget was already instantiated earlier in this run.
                # Stash the result and rerun; the pending-model block near
                # the top of the script applies it before the widget is
                # (re)created on the next run.
                st.session_state["_pending_model"] = enriched
                st.session_state["enrichment_message"] = (
                    "AI context merged into the YAML below (synonyms/instructions "
                    "concatenated, custom extensions appended)."
                )
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"Failed to enrich the semantic model: {e}")
                with st.expander("Show details"):
                    st.code(traceback.format_exc())

    if st.session_state.enrichment_message:
        st.success(st.session_state.enrichment_message)
        st.session_state.enrichment_message = None

st.divider()
with st.expander("About the Apache Ossie spec & this app's mapping choices"):
    st.markdown(
        """
Apache Ossie ("Open Semantic Interchange") defines a vendor-neutral
YAML/JSON spec for semantic models: `datasets` (fact/dimension tables),
`fields` (columns), `relationships` (foreign keys), `metrics`, and
`ai_context` throughout. See the
[core specification](https://github.com/apache/ossie/tree/main/core-spec)
for the full schema.

Because the Ossie `Field` and `Dataset` objects only accept a fixed set of
properties (`additionalProperties: false`), source-system attributes that
aren't part of the core spec -- **size**, **raw technical data type**,
**is nullable**, **contains PII**, and **column position** -- are preserved
losslessly inside standard Ossie `custom_extensions` blocks
(`vendor_name: COMMON`), so nothing from your source files is discarded
even though it isn't a first-class Ossie field.

**Two-stage workflow**

1. **Base generation** (Files 1-3): Table/column metadata is required.
   The metrics/synonyms/custom-extensions file and the relationships file
   are both optional and only add to the base YAML. Once generated, you
   can view and hand-edit the YAML directly in this app -- click
   **Apply edits** to re-validate and lock in your changes.
2. **AI-context enrichment** (File 4, optional, uploaded later): merges
   into whatever base YAML currently exists (freshly generated *or*
   hand-edited). It only **adds**: new instructions are appended to any
   existing instructions, new synonyms/examples are appended (and
   de-duplicated), and any `Custom Extension` cell becomes a brand-new
   `custom_extensions` entry (`vendor_name: AI_ENRICHMENT`) rather than
   replacing what's there.

**Mapping summary**

- **Name** &rarr; dataset name · **Column Title** &rarr; field name
- **Description** &rarr; field `description` (business-friendly)
- **Description from source system**, **size**, **Technical Data Type**,
  **Column Position**, **Is nullable**, **Contains PII** &rarr; field
  `custom_extensions` (vendor `COMMON`)
- **Technical Data Type** is also best-effort mapped to the Ossie
  `datatype` enum (`String`, `Integer`, `Decimal`, `Float`, `Boolean`,
  `Date`, `Time`, `DateTime`, `DateTimeTz`, `Opaque`)
- **Is Primary Key** (+ **Primary Key** group label) &rarr; dataset
  `primary_key` (supports composite keys, e.g. the `FACT_POSITION` grain of
  `ACCOUNT_ID` + `SECURITY_ID` + `AS_OF_DATE_ID` in the sample data)
- **Assest Type** (Fact/Dimension Table) &rarr; dataset `custom_extensions`
- **Metric Name/Expression/Description/Data Type** rows &rarr; native
  Ossie `metrics[]` at the model level
- **Synonym** rows &rarr; field-level `ai_context.synonyms` (base synonyms)
- **Custom Extension** rows (Table Name only = table-level; + Column Name
  = field-level) &rarr; `custom_extensions` on that dataset/field
- Relationships file &rarr; native Ossie `relationships`
- AI context file &rarr; concatenated into `ai_context` and appended into
  `custom_extensions` at the model/dataset/field level (Stage 2 only)
        """
    )
