"""Streamlit app: upload table/column metadata, snapshot details,
relationships, and AI context files (CSV / XLSX / TXT) and generate an
Apache Ossie (Open Semantic Interchange) semantic model YAML document.

Spec reference: https://github.com/apache/ossie/tree/main/core-spec
"""

import os
import traceback

import pandas as pd
import streamlit as st

import ossie_builder as ob

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT_DIR, "schema", "ossie-schema.json")
SAMPLE_DIR = os.path.join(ROOT_DIR, "sample_data")

SAMPLE_FILES = {
    "metadata": "01_table_column_metadata.csv",
    "snapshot": "02_snapshot_details.csv",
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


if "use_sample" not in st.session_state:
    st.session_state.use_sample = False

# ---------------------------------------------------------------------------
# Sidebar: model settings + sample data
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
            st.session_state.use_sample = True
    with col_b:
        if st.button("Clear sample data", use_container_width=True):
            st.session_state.use_sample = False

    with st.expander("Download blank templates", expanded=False):
        for key, label in [
            ("metadata", "1. Table/Column metadata"),
            ("snapshot", "2. Snapshot details"),
            ("relationships", "3. Relationships"),
            ("ai_context", "4. AI context"),
        ]:
            st.download_button(
                label=f"⬇️ {label} sample CSV",
                data=_read_sample(key),
                file_name=SAMPLE_FILES[key],
                mime="text/csv",
                use_container_width=True,
                key=f"dl_{key}",
            )

st.title("🧬 Apache Ossie Semantic Model Builder")
st.markdown(
    "Upload table metadata, snapshot details, relationships, and AI-context "
    "spreadsheets, then generate a validated **Apache Ossie** "
    "([core-spec](https://github.com/apache/ossie/tree/main/core-spec)) "
    "semantic model YAML file -- ready for BI tools and AI agents."
)

# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------

st.subheader("1. Upload your files")
st.caption("Accepted formats: **.csv, .xlsx, .txt** (delimited). Multi-sheet Excel workbooks are combined automatically.")

upload_cols = st.columns(4)

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
    st.markdown("**② Snapshot details** `optional`")
    st.caption(
        "One row per table: Table Name, Snapshot Type, Snapshot Frequency, "
        "Snapshot Date Column, Partition Column, History Type, Retention "
        "Period, Source System, Load Pattern."
    )
    snapshot_upload = st.file_uploader(
        "Snapshot file", type=["csv", "xlsx", "txt"], key="snapshot_upload", label_visibility="collapsed"
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

with upload_cols[3]:
    st.markdown("**④ AI context / instructions** `optional`")
    st.caption(
        "One row per table or column: Table Name, Column Name (blank = "
        "table-level; 'MODEL' = model-level), Instructions, Synonyms, "
        "Examples."
    )
    ai_context_upload = st.file_uploader(
        "AI context file", type=["csv", "xlsx", "txt"], key="ai_context_upload", label_visibility="collapsed"
    )

use_sample = st.session_state.use_sample
if use_sample:
    st.info(
        "Using the bundled **Account / Position** sample data. Click "
        "**Clear sample data** in the sidebar to switch back to your own uploads.",
        icon="ℹ️",
    )

try:
    metadata_df = _load_df_from_sample("metadata") if use_sample else _load_df_from_upload(metadata_upload)
    snapshot_df = _load_df_from_sample("snapshot") if use_sample else _load_df_from_upload(snapshot_upload)
    relationships_df = _load_df_from_sample("relationships") if use_sample else _load_df_from_upload(relationships_upload)
    ai_context_df = _load_df_from_sample("ai_context") if use_sample else _load_df_from_upload(ai_context_upload)
except Exception as e:  # noqa: BLE001
    st.error(f"Failed to read one of the uploaded files: {e}")
    st.stop()

if metadata_df is None:
    st.warning("Upload the table/column metadata file (or load the sample data) to continue.")
    st.stop()

# ---------------------------------------------------------------------------
# Preview parsed data
# ---------------------------------------------------------------------------

st.subheader("2. Preview parsed input")
preview_tabs = st.tabs(["Metadata", "Snapshot details", "Relationships", "AI context"])
with preview_tabs[0]:
    st.dataframe(metadata_df, use_container_width=True, height=260)
with preview_tabs[1]:
    if snapshot_df is not None:
        st.dataframe(snapshot_df, use_container_width=True, height=220)
    else:
        st.caption("No snapshot details file uploaded.")
with preview_tabs[2]:
    if relationships_df is not None:
        st.dataframe(relationships_df, use_container_width=True, height=220)
    else:
        st.caption("No relationships file uploaded.")
with preview_tabs[3]:
    if ai_context_df is not None:
        st.dataframe(ai_context_df, use_container_width=True, height=220)
    else:
        st.caption("No AI context file uploaded.")

# ---------------------------------------------------------------------------
# Parse + build
# ---------------------------------------------------------------------------

st.subheader("3. Generate the Ossie semantic model")

generate = st.button("🚀 Generate Ossie YAML", type="primary")

if generate:
    try:
        tables = ob.parse_metadata(metadata_df)
        snapshot_details = ob.parse_snapshot_details(snapshot_df)
        relationships = ob.parse_relationships(relationships_df)
        model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(ai_context_df)

        result = ob.build_semantic_model(
            model_name=model_name.strip() or "semantic_model",
            model_description=model_description.strip(),
            dialect=dialect,
            source_prefix=source_prefix.strip().rstrip("."),
            tables=tables,
            snapshot_details=snapshot_details,
            relationships=relationships,
            model_ai_context=model_ctx,
            dataset_ai_context=dataset_ctx,
            field_ai_context=field_ctx,
        )

        yaml_text = ob.to_yaml(result.model)

        n_tables = len(tables)
        n_fields = sum(len(t.columns) for t in tables.values())
        n_rels = len(result.model["semantic_model"][0].get("relationships", []))

        metric_cols = st.columns(3)
        metric_cols[0].metric("Datasets", n_tables)
        metric_cols[1].metric("Fields", n_fields)
        metric_cols[2].metric("Relationships", n_rels)

        if result.warnings:
            for w in result.warnings:
                st.warning(w)

        errors = ob.validate_model(result.model, SCHEMA_PATH)
        if errors:
            st.error("The generated YAML did NOT pass validation against the Ossie JSON Schema:")
            for err in errors:
                st.code(err, language="text")
        else:
            st.success("✅ Generated YAML is valid against the Apache Ossie core-spec JSON Schema.")

        st.code(yaml_text, language="yaml", line_numbers=True)

        st.download_button(
            label="⬇️ Download ossie_semantic_model.yaml",
            data=yaml_text,
            file_name=f"{(model_name.strip() or 'semantic_model')}.ossie.yaml",
            mime="application/x-yaml",
            type="primary",
        )

    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to generate the semantic model: {e}")
        with st.expander("Show details"):
            st.code(traceback.format_exc())

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
**is nullable**, **contains PII**, **column position**, **description from
source system**, and the **snapshot** metadata you provide -- are preserved
losslessly inside standard Ossie `custom_extensions` blocks
(`vendor_name: COMMON` / `SNAPSHOT`), so nothing from your source files is
discarded even though it isn't a first-class Ossie field.

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
- Snapshot file &rarr; dataset `custom_extensions` (vendor `SNAPSHOT`)
- Relationships file &rarr; native Ossie `relationships`
- AI context file &rarr; native Ossie `ai_context` at the model, dataset,
  and field level
        """
    )
