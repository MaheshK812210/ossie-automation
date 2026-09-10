"""Streamlit app: a two-stage Apache Ossie (Open Semantic Interchange)
semantic model builder with a persistent, editable YAML panel.

Layout
------
- Left column: Stage 1 (upload -> tree-style review/selection -> generate)
  followed by Stage 2 (optional, later AI-context enrichment).
- Right column: a persistent, expandable "View & edit YAML" panel shared by
  both stages -- whatever Stage 1 generates or Stage 2 enriches shows up
  here immediately, and manual edits applied here feed back into Stage 2.

Spec reference: https://github.com/apache/ossie/tree/main/core-spec
"""

import os
import traceback
from typing import Any, Dict, List, Tuple

import streamlit as st
import yaml

import ossie_builder as ob
import powerbi_export as pbe

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(ROOT_DIR, "schema", "ossie-schema.json")
SAMPLE_DIR = os.path.join(ROOT_DIR, "sample_data")

SAMPLE_FILES = {
    "metadata": "01_table_column_metadata.csv",
    "enrichment": "02_metrics_synonyms_extensions.csv",
    "relationships": "03_relationships.csv",
    "ai_context": "04_ai_context.csv",
}

DATATYPE_COLORS = {
    "String": "blue",
    "Integer": "green",
    "Decimal": "green",
    "Float": "green",
    "Boolean": "violet",
    "Date": "orange",
    "Time": "orange",
    "DateTime": "orange",
    "DateTimeTz": "orange",
    "Opaque": "gray",
}

st.set_page_config(
    page_title="Ossie Semantic Model Builder",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
/* Catchy layered gradient backdrop instead of plain white */
.stApp {
    background: radial-gradient(circle at 8% 0%, #EAE3FF 0%, transparent 42%),
                radial-gradient(circle at 100% 12%, #FFE6F5 0%, transparent 38%),
                radial-gradient(circle at 20% 100%, #E1F4FF 0%, transparent 45%),
                linear-gradient(160deg, #F7F4FF 0%, #F3EEFF 50%, #FBF0FA 100%);
    background-attachment: fixed;
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #EEE7FD 0%, #E3D9FB 100%);
}
[data-testid="stSidebar"] > div:first-child {
    border-right: 1px solid #D8CCF8;
}

.ossie-hero {
    background: linear-gradient(135deg, #6C5CE7 0%, #A29BFE 55%, #FD79C6 130%);
    padding: 1.6rem 2rem;
    border-radius: 16px;
    color: white;
    margin-bottom: 1.3rem;
    box-shadow: 0 8px 24px rgba(108, 92, 231, 0.25);
}
.ossie-hero h1 { margin: 0; font-size: 1.85rem; }
.ossie-hero p { margin: 0.45rem 0 0 0; opacity: 0.96; font-size: 1.02rem; line-height: 1.5; }
.ossie-hero code { background: rgba(255,255,255,0.2); color: white; padding: 0.05rem 0.35rem; border-radius: 4px; }

/* White "card" surfaces so content pops against the tinted background */
div[data-testid="stExpander"] {
    background-color: #FFFFFF;
    border: 1px solid #E4DEFB;
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(108, 92, 231, 0.10);
}
div[data-testid="stExpander"] summary {
    font-weight: 600;
}
button[kind="primary"] {
    border-radius: 8px;
    box-shadow: 0 2px 6px rgba(108, 92, 231, 0.35);
}
div[data-testid="stMetric"] {
    background-color: #FFFFFF;
    border-radius: 10px;
    padding: 0.6rem 0.8rem 0.3rem 0.8rem;
    border: 1px solid #E4DEFB;
    border-left: 4px solid #6C5CE7;
    box-shadow: 0 1px 4px rgba(108, 92, 231, 0.08);
}
div[data-testid="stTextArea"] textarea {
    background-color: #FFFFFF;
    border-radius: 10px;
}
div[data-testid="stDataFrame"], div[data-testid="stTable"] {
    background-color: #FFFFFF;
    border-radius: 10px;
    overflow: hidden;
}
div[data-testid="stTabs"] button[role="tab"] {
    border-radius: 8px 8px 0 0;
}
hr { margin: 0.6rem 0; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# File / model helpers
# ---------------------------------------------------------------------------

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


def _dataset_field_counts(model: Dict[str, Any]):
    entry = model["semantic_model"][0]
    n_datasets = len(entry.get("datasets", []))
    n_fields = sum(len(d.get("fields", [])) for d in entry.get("datasets", []))
    n_rels = len(entry.get("relationships", []))
    n_metrics = len(entry.get("metrics", []))
    return n_datasets, n_fields, n_rels, n_metrics


def _show_validation(model: Dict[str, Any]) -> List[str]:
    errors = ob.validate_model(model, SCHEMA_PATH)
    if errors:
        st.error("This YAML does NOT pass validation against the Ossie JSON Schema:")
        for err in errors:
            st.code(err, language="text")
    else:
        st.success("✅ Valid against the Apache Ossie core-spec JSON Schema.")
    return errors


def _asset_icon(asset_type: str) -> str:
    a = (asset_type or "").lower()
    if "fact" in a:
        return "📊"
    if "dim" in a:
        return "🧩"
    if "view" in a:
        return "🔎"
    return "📄"


def _filter_tables(
    tables: Dict[str, "ob.TableMeta"], selected: Dict[Tuple[str, str], bool]
) -> Tuple[Dict[str, "ob.TableMeta"], List[str]]:
    filtered: Dict[str, "ob.TableMeta"] = {}
    dropped: List[str] = []
    for name, table in tables.items():
        kept_cols = [c for c in table.columns if selected.get((name, c.column_name), True)]
        if not kept_cols:
            dropped.append(name)
            continue
        filtered[name] = ob.TableMeta(table_name=table.table_name, asset_type=table.asset_type, columns=kept_cols)
    return filtered, dropped


# ---------------------------------------------------------------------------
# Tree-style preview & selection widgets
# ---------------------------------------------------------------------------

def _render_metadata_tree(tables: Dict[str, "ob.TableMeta"]) -> Dict[Tuple[str, str], bool]:
    selection: Dict[Tuple[str, str], bool] = {}
    for table_name, table in tables.items():
        header = f"{_asset_icon(table.asset_type)} **{table_name}**  ·  {table.asset_type or 'Table'}  ·  {len(table.columns)} columns"
        with st.expander(header, expanded=False):
            bcol1, bcol2, _sp = st.columns([1, 1, 3])
            with bcol1:
                if st.button("Select all", key=f"selall__{table_name}", use_container_width=True):
                    for c in table.columns:
                        st.session_state[f"colsel__{table_name}__{c.column_name}"] = True
            with bcol2:
                if st.button("Select none", key=f"selnone__{table_name}", use_container_width=True):
                    for c in table.columns:
                        st.session_state[f"colsel__{table_name}__{c.column_name}"] = False
            st.markdown("---")
            for c in table.columns:
                key = f"colsel__{table_name}__{c.column_name}"
                row = st.columns([0.4, 2.6, 1.4, 4.6])
                with row[0]:
                    checked = st.checkbox(
                        "sel", key=key, value=True, label_visibility="collapsed"
                    )
                with row[1]:
                    badges = []
                    if c.is_primary_key:
                        badges.append(":orange-badge[🔑 PK]")
                    if c.contains_pii:
                        badges.append(":red-badge[🔒 PII]")
                    if not c.is_nullable:
                        badges.append(":gray-badge[NOT NULL]")
                    st.markdown(f"`{c.column_name}`  " + " ".join(badges))
                with row[2]:
                    dt = ob.map_technical_datatype(c.technical_data_type)
                    st.badge(dt, color=DATATYPE_COLORS.get(dt, "gray"))
                with row[3]:
                    st.caption(c.description or c.technical_data_type or "\u00a0")
                selection[(table_name, c.column_name)] = checked
    return selection


def _render_metrics_tree(metrics: List[Dict[str, str]]) -> Dict[str, bool]:
    selection: Dict[str, bool] = {}
    if not metrics:
        st.caption("No metrics parsed from the metrics/synonyms/extensions file.")
        return selection

    header = f"📐 **Metrics**  ·  {len(metrics)} defined"
    with st.expander(header, expanded=False):
        bcol1, bcol2, _sp = st.columns([1, 1, 3])
        with bcol1:
            if st.button("Select all", key="metric_selall", use_container_width=True):
                for m in metrics:
                    st.session_state[f"metricsel__{m['name']}"] = True
        with bcol2:
            if st.button("Select none", key="metric_selnone", use_container_width=True):
                for m in metrics:
                    st.session_state[f"metricsel__{m['name']}"] = False
        st.markdown("---")
        for m in metrics:
            key = f"metricsel__{m['name']}"
            row = st.columns([0.4, 3.2, 5.4])
            with row[0]:
                checked = st.checkbox("sel", key=key, value=True, label_visibility="collapsed")
            with row[1]:
                dt = m.get("datatype") or ""
                dt_badge = f"  :green-badge[{dt}]" if dt else ""
                st.markdown(f"`{m['name']}`{dt_badge}")
                st.caption(m.get("expression", ""))
            with row[2]:
                st.caption(m.get("description") or "\u00a0")
            selection[m["name"]] = checked
    return selection


def _dot_escape(s: str) -> str:
    return s.replace('"', '\\"')


def _build_relationship_dot(relationships: List[Dict[str, Any]], tables: Dict[str, "ob.TableMeta"]) -> str:
    lines = [
        "digraph G {",
        '  rankdir=LR;',
        '  bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fillcolor="#F5F3FF", '
        'color="#6C5CE7", fontname="Helvetica", fontsize=11, margin=0.18];',
        '  edge [color="#6C5CE7", fontname="Helvetica", fontsize=9, fontcolor="#4B4B4B"];',
    ]
    involved = set()
    for r in relationships:
        involved.add(r["from_table"])
        involved.add(r["to_table"])
    for t in sorted(involved):
        pk_cols = [c.column_name for c in tables[t].columns if c.is_primary_key] if t in tables else []
        label = _dot_escape(t)
        if pk_cols:
            label += f"\\n🔑 {_dot_escape(', '.join(pk_cols))}"
        lines.append(f'  "{_dot_escape(t)}" [label="{label}"];')
    for r in relationships:
        label = f"{', '.join(r['from_columns'])} → {', '.join(r['to_columns'])}"
        if r.get("relationship_type"):
            label += f"\\n({r['relationship_type']})"
        lines.append(
            f'  "{_dot_escape(r["from_table"])}" -> "{_dot_escape(r["to_table"])}" '
            f'[label="{_dot_escape(label)}"];'
        )
    lines.append("}")
    return "\n".join(lines)


def _render_yaml_panel(height: int):
    """The persistent, expandable View & Edit YAML panel. Shared by both
    the two-column layout and the "left pane minimized" full-width layout.
    """
    st.header("\U0001f4c4 Ossie YAML")
    if st.session_state.model is None:
        st.info(
            "Your generated YAML will appear here once you run **Generate base YAML** "
            "in Stage 1. This panel stays open across both stages so you can view and "
            "edit it at any time.",
            icon="\U0001f4dd",
        )
        return

    with st.expander("View & edit YAML", expanded=True):
        n_datasets, n_fields, n_rels, n_metrics = _dataset_field_counts(st.session_state.model)
        m = st.columns(4)
        m[0].metric("Datasets", n_datasets)
        m[1].metric("Fields", n_fields)
        m[2].metric("Relations", n_rels)
        m[3].metric("Metrics", n_metrics)

        toolbar = st.columns([1, 1, 2])
        with toolbar[0]:
            tall_label = "\U0001f53d Normal height" if st.session_state.yaml_panel_tall else "\U0001f53c Expand height"
            if st.button(tall_label, use_container_width=True, key="yaml_tall_toggle"):
                st.session_state.yaml_panel_tall = not st.session_state.yaml_panel_tall
                st.rerun()

        st.text_area("Ossie YAML", key="yaml_editor", height=height, label_visibility="collapsed")

        c1, c2 = st.columns(2)
        with c1:
            apply_edits = st.button("\u2705 Apply edits", use_container_width=True)
        with c2:
            st.download_button(
                label="\u2b07\ufe0f Download YAML",
                data=st.session_state.yaml_editor,
                file_name=f"{(model_name.strip() or 'semantic_model')}.ossie.yaml",
                mime="application/x-yaml",
                use_container_width=True,
            )

        if apply_edits:
            try:
                parsed = ob.parse_yaml_text(st.session_state.yaml_editor)
                st.session_state.model = parsed
                st.success("Edits applied.")
            except (yaml.YAMLError, ValueError) as e:
                st.error(f"Could not parse your edits as valid YAML: {e}")

        _show_validation(st.session_state.model)


def _render_relationships_section(relationships: List[Dict[str, Any]], tables: Dict[str, "ob.TableMeta"]):
    if not relationships:
        st.caption("No relationships parsed from the relationships file.")
        return
    st.caption("Primary key \u2194 foreign key relationships between tables:")
    dot = _build_relationship_dot(relationships, tables)
    st.graphviz_chart(dot, use_container_width=True)

    rows = []
    for r in relationships:
        rows.append(
            {
                "Relationship": r["name"],
                "From table (FK)": f"{r['from_table']}.{', '.join(r['from_columns'])}",
                "\u2192": "\u2192",
                "To table (PK/UK)": f"{r['to_table']}.{', '.join(r['to_columns'])}",
                "Type": r.get("relationship_type") or "\u2014",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

st.session_state.setdefault("use_sample_base", False)
st.session_state.setdefault("use_sample_ai_context", False)
st.session_state.setdefault("model", None)
st.session_state.setdefault("yaml_editor", "")
st.session_state.setdefault("enrichment_message", None)
st.session_state.setdefault("left_pane_collapsed", False)
st.session_state.setdefault("yaml_panel_tall", False)
st.session_state.setdefault("pbi_export", None)
st.session_state.setdefault("pbi_metric_preview", None)

# A widget's session_state value can only be set BEFORE that widget is
# instantiated in a given script run. Stage 2 computes its result after the
# "yaml_editor" text_area has already been created, so it stashes the new
# model here and triggers a rerun; this block -- which always runs before
# the text_area is (re)created -- is what actually applies it.
if st.session_state.get("_pending_model") is not None:
    st.session_state.model = st.session_state.pop("_pending_model")
    st.session_state.yaml_editor = ob.to_yaml(st.session_state.model)

# ---------------------------------------------------------------------------
# Sidebar: model settings + sample data + templates
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Semantic model settings")
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
    st.header("🧪 Try it with sample data")
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

    with st.expander("⬇️ Download blank templates", expanded=False):
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
        for k in list(st.session_state.keys()):
            if k.startswith(("colsel__", "metricsel__", "selall__", "selnone__")):
                del st.session_state[k]
        st.session_state.model = None
        st.session_state.yaml_editor = ""
        st.session_state.use_sample_base = False
        st.session_state.use_sample_ai_context = False
        st.session_state.pbi_export = None
        st.session_state.pbi_metric_preview = None
        st.rerun()

def _render_stage_1_and_2():
    st.header("Stage 1 \u2014 Generate the base YAML")
    st.subheader("1. Upload your files")
    st.caption(
        "Accepted formats: **.csv, .xlsx, .txt** (delimited). Multi-sheet Excel workbooks are combined automatically."
    )

    upload_cols = st.columns(3)
    with upload_cols[0]:
        st.markdown("**\u2460 Table & column metadata** `required`")
        st.caption(
            "One row per column: Name, Assest Type, Column Title, Description, "
            "Description from source system, size, Technical Data Type, Column "
            "Position, Is Primary Key, Is nullable, Contains PII, Primary Key."
        )
        metadata_upload = st.file_uploader(
            "Metadata file", type=["csv", "xlsx", "txt"], key="metadata_upload", label_visibility="collapsed"
        )
    with upload_cols[1]:
        st.markdown("**\u2461 Metrics, synonyms & custom extensions** `optional`")
        st.caption(
            "One row per item, `Type` = Metric / Synonym / Custom Extension: "
            "Table Name, Column Name, Metric Name/Expression/Description/Data "
            "Type, Synonyms, Custom Extension Vendor/Data."
        )
        enrichment_upload = st.file_uploader(
            "Metrics/synonyms/extensions file", type=["csv", "xlsx", "txt"],
            key="enrichment_upload", label_visibility="collapsed",
        )
    with upload_cols[2]:
        st.markdown("**\u2462 Relationships** `optional`")
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
            icon="\u2139\ufe0f",
        )

    try:
        metadata_df = _load_df_from_sample("metadata") if use_sample_base else _load_df_from_upload(metadata_upload)
        enrichment_df = _load_df_from_sample("enrichment") if use_sample_base else _load_df_from_upload(enrichment_upload)
        relationships_df = _load_df_from_sample("relationships") if use_sample_base else _load_df_from_upload(relationships_upload)
        load_error = None
    except Exception as e:  # noqa: BLE001
        metadata_df = enrichment_df = relationships_df = None
        load_error = str(e)

    if load_error:
        st.error(f"Failed to read one of the uploaded files: {load_error}")
    elif metadata_df is None:
        st.warning("Upload the table/column metadata file (or load the sample data) to continue.")
    else:
        parse_error = None
        try:
            tables = ob.parse_metadata(metadata_df)
            enrichment = ob.parse_metrics_synonyms_extensions(enrichment_df)
            relationships = ob.parse_relationships(relationships_df)
        except Exception as e:  # noqa: BLE001
            parse_error = str(e)
            tables, enrichment, relationships = None, None, None

        if parse_error:
            st.error(f"Failed to parse the uploaded files: {parse_error}")
        else:
            st.subheader("2. Review & select what to include")
            st.caption(
                "Expand each table to uncheck columns you don't want in the YAML. "
                "Deselecting every column in a table drops that table entirely."
            )
            tree_tabs = st.tabs(["\U0001f9f1 Tables & columns", "\U0001f4d0 Metrics", "\U0001f517 Relationships"])
            with tree_tabs[0]:
                selected_columns = _render_metadata_tree(tables)
            with tree_tabs[1]:
                selected_metrics = _render_metrics_tree(enrichment.metrics)
            with tree_tabs[2]:
                _render_relationships_section(relationships, tables)

            for w in enrichment.warnings:
                st.warning(w)

            st.subheader("3. Generate the base Ossie YAML")
            generate = st.button("\U0001f680 Generate base YAML", type="primary")

            if generate:
                try:
                    filtered_tables, dropped_tables = _filter_tables(tables, selected_columns)
                    filtered_metrics = [m for m in enrichment.metrics if selected_metrics.get(m["name"], True)]

                    for name in dropped_tables:
                        st.warning(f"Table '{name}' excluded from the YAML \u2014 no columns were selected.")

                    result = ob.build_semantic_model(
                        model_name=model_name.strip() or "semantic_model",
                        model_description=model_description.strip(),
                        dialect=dialect,
                        source_prefix=source_prefix.strip().rstrip("."),
                        tables=filtered_tables,
                        relationships=relationships,
                        metrics=filtered_metrics,
                        field_synonyms=enrichment.field_synonyms,
                        dataset_extensions=enrichment.dataset_extensions,
                        field_extensions=enrichment.field_extensions,
                    )

                    for w in result.warnings:
                        st.warning(w)

                    st.session_state.model = result.model
                    st.session_state.yaml_editor = ob.to_yaml(result.model)
                    st.toast("Base YAML generated \u2014 see the panel on the right.", icon="\u2705")
                    st.rerun()

                except Exception as e:  # noqa: BLE001
                    st.error(f"Failed to generate the semantic model: {e}")
                    with st.expander("Show details"):
                        st.code(traceback.format_exc())

    st.divider()
    st.header("Stage 2 \u2014 Enrich with AI context (optional, later)")

    if st.session_state.model is None:
        st.caption("Generate a base YAML in Stage 1 first \u2014 this section unlocks once one exists.")
    else:
        st.caption(
            "Upload this **after** you already have a base YAML (freshly generated or hand-edited "
            "in the panel on the right). It never overwrites existing instructions/synonyms/examples "
            "\u2014 it **concatenates** new ones onto whatever a table/field/model already has, and "
            "**appends** any `Custom Extension` value as a new `custom_extensions` entry "
            "(`vendor_name: AI_ENRICHMENT`)."
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
            st.info("Using the bundled sample AI-context file.", icon="\u2139\ufe0f")

        try:
            ai_context_df = _load_df_from_sample("ai_context") if use_sample_ai else _load_df_from_upload(ai_context_upload)
        except Exception as e:  # noqa: BLE001
            st.error(f"Failed to read the AI context file: {e}")
            ai_context_df = None

        if ai_context_df is not None:
            with st.expander("Preview AI context rows", expanded=False):
                st.dataframe(ai_context_df, use_container_width=True, height=220)

            if st.button("\U0001f9e0 Enrich YAML with AI context", type="primary"):
                try:
                    model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(ai_context_df)
                    enriched = ob.merge_ai_context_into_model(
                        st.session_state.model, model_ctx, dataset_ctx, field_ctx
                    )
                    # Can't touch st.session_state.yaml_editor directly here --
                    # that widget was already instantiated earlier in this run.
                    st.session_state["_pending_model"] = enriched
                    st.session_state["enrichment_message"] = (
                        "AI context merged into the YAML panel (synonyms/instructions "
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
    st.header("Stage 3 \u2014 Export to a BI tool (optional)")

    if st.session_state.model is None:
        st.caption("Generate a base YAML in Stage 1 first \u2014 this section unlocks once one exists.")
    else:
        st.caption(
            "Convert the current Ossie semantic model into a target BI tool's own semantic model "
            "format, with one click \u2014 no desktop application required to generate it."
        )
        bi_tabs = st.tabs(["\U0001f7e6 Power BI", "\U0001f4ca Tableau"])

        with bi_tabs[0]:
            st.markdown(
                "Generates a real **Power BI / Fabric semantic model**: TMSL (`model.bim`) plus a "
                "TMDL-based Power BI Project folder. Open the folder directly in Power BI Desktop "
                "via *File \u2192 Open \u2192 Power BI Project*, commit it to git for Fabric's git "
                "integration, or deploy it headlessly with the Tabular Editor CLI / Fabric REST API "
                "\u2014 none of that requires generating it from a desktop app."
            )
            if st.button("\U0001f504 Convert to Power BI semantic model", type="primary", key="convert_pbi"):
                try:
                    st.session_state["pbi_export"] = pbe.convert_to_powerbi(
                        st.session_state.model, model_name.strip() or "semantic_model"
                    )
                    st.session_state["pbi_metric_preview"] = None
                except Exception as e:  # noqa: BLE001
                    st.error(f"Failed to convert to a Power BI semantic model: {e}")
                    with st.expander("Show details"):
                        st.code(traceback.format_exc())

            export = st.session_state.get("pbi_export")
            if export is not None:
                n_tables = len(export.tmsl["model"]["tables"])
                n_measures = sum(len(t.get("measures", [])) for t in export.tmsl["model"]["tables"])
                n_rels = len(export.tmsl["model"].get("relationships", []))
                mcols = st.columns(3)
                mcols[0].metric("Tables", n_tables)
                mcols[1].metric("Measures (DAX)", n_measures)
                mcols[2].metric("Relationships", n_rels)

                st.download_button(
                    "\u2b07\ufe0f Download Power BI Project (.zip)",
                    data=export.zip_bytes,
                    file_name=f"{(model_name.strip() or 'semantic_model')}.SemanticModel.zip",
                    mime="application/zip",
                    type="primary",
                    key="dl_pbi_zip",
                )

                pbi_view_tabs = st.tabs(["model.bim (TMSL)", "TMDL files", "\u25b6\ufe0f Preview metrics (DAX)"])
                with pbi_view_tabs[0]:
                    st.code(export.tmsl_json, language="json", line_numbers=True)
                with pbi_view_tabs[1]:
                    file_choice = st.selectbox("File", list(export.tmdl_files.keys()), key="tmdl_file_choice")
                    st.code(export.tmdl_files[file_choice], language="text")
                with pbi_view_tabs[2]:
                    st.caption(
                        "There's no live Power BI service or data warehouse connected in this "
                        "environment, so these values are computed against small, randomly "
                        "generated **synthetic sample data** matching your schema \u2014 purely to "
                        "prove the metric logic (and its SQL \u2192 DAX translation) runs end-to-end. "
                        "They are illustrative, not real business results."
                    )
                    if st.button("\u25b6\ufe0f Run metrics against synthetic sample data", key="run_metrics_pbi"):
                        try:
                            synth = pbe.generate_synthetic_data(st.session_state.model)
                            st.session_state["pbi_metric_preview"] = pbe.evaluate_metrics(
                                st.session_state.model, synth
                            )
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Failed to evaluate metrics: {e}")
                            with st.expander("Show details"):
                                st.code(traceback.format_exc())

                    preview = st.session_state.get("pbi_metric_preview")
                    if preview:
                        rows = [
                            {
                                "Metric": r["name"],
                                "DAX expression": r["dax_expression"],
                                "Simulated value": r["value"] if r["error"] is None else "\u2014",
                                "Note": r["error"] or "",
                            }
                            for r in preview
                        ]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    elif preview == []:
                        st.caption("No metrics are defined in this model yet.")

        with bi_tabs[1]:
            st.info(
                "**Tableau data model export isn't built in this app yet** \u2014 only the Power BI "
                "path is implemented for now. The same Ossie YAML could similarly drive a Tableau "
                "data source (`.tds`/`.tdsx` XML: tables, joins, and calculated fields translated "
                "into Tableau's calculation language) as a future addition.",
                icon="\U0001f6a7",
            )

    st.divider()
    with st.expander("\u2139\ufe0f About the Apache Ossie spec & this app's mapping choices"):
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

1. **Base generation** (Files 1-3): Table/column metadata is required. The
   metrics/synonyms/custom-extensions file and the relationships file are
   both optional. Use the tree view to uncheck columns/metrics you don't
   want before generating -- deselected columns (and tables left with zero
   selected columns) are excluded from the YAML.
2. **AI-context enrichment** (File 4, optional, uploaded later): merges
   into whatever base YAML currently exists (freshly generated *or*
   hand-edited in the panel on the right). It only **adds**.
3. **BI tool export** (Stage 3, optional): converts the current model into
   a target BI tool's own semantic model format. Only **Power BI** is
   implemented -- it produces real TMSL (`model.bim`) and a TMDL-based
   Power BI Project, with a local, synthetic-data preview of the DAX
   measures so you can see them execute without a live Power BI/Fabric
   workspace. Tableau shows as a planned option, not yet built.

**Mapping summary**

- **Name** &rarr; dataset name &middot; **Column Title** &rarr; field name
- **Description** &rarr; field `description`
- **Description from source system**, **size**, **Technical Data Type**,
  **Column Position**, **Is nullable**, **Contains PII** &rarr; field
  `custom_extensions` (vendor `COMMON`)
- **Technical Data Type** is also best-effort mapped to the Ossie
  `datatype` enum
- **Is Primary Key** (+ **Primary Key** group label) &rarr; dataset
  `primary_key` (supports composite keys)
- **Assest Type**, PII column roll-up &rarr; dataset `custom_extensions`
- File 2 `Metric` rows &rarr; native `semantic_model[].metrics[]`
- File 2 `Synonym` rows &rarr; field `ai_context.synonyms` (base synonyms)
- File 2 `Custom Extension` rows &rarr; `custom_extensions` on the dataset
  or field
- Relationships file &rarr; native `semantic_model[].relationships[]`
  (also visualized as a diagram above)
- File 4 AI context (Stage 2) &rarr; concatenated into `ai_context` +
  appended into `custom_extensions` (`vendor_name: AI_ENRICHMENT`)
            """
        )



# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------

st.markdown(
    """
<div class="ossie-hero">
  <h1>🧬 Apache Ossie Semantic Model Builder</h1>
  <p><b>Stage 1</b>: upload table metadata, metrics/synonyms, and relationships to generate a base
  <code>Apache Ossie</code> semantic model YAML, review it in a tree view with checkboxes, then view
  &amp; edit the YAML live. <b>Stage 2</b> (later, optional): enrich it further with AI context.
  <b>Stage 3</b> (optional): export it straight into a <b>Power BI</b> semantic model.</p>
</div>
""",
    unsafe_allow_html=True,
)

left_collapsed = st.session_state.left_pane_collapsed
column_ratio = [0.09, 1.91] if left_collapsed else [1.7, 1]
main_col, yaml_col = st.columns(column_ratio, gap="large")

# Whenever the left pane is minimized (or the user explicitly asked for a
# taller box), give the YAML panel a lot more vertical room so it can show
# as much as possible -- covering roughly what Stage 1 + Stage 2 take up --
# without needing its own separate scroll.
yaml_panel_height = 1150 if (left_collapsed or st.session_state.yaml_panel_tall) else 650

# ===========================================================================
# LEFT COLUMN -- Stage 1 + Stage 2 workflow (collapsible)
# ===========================================================================

with main_col:
    if left_collapsed:
        if st.button("\u25b6", help="Expand Stage 1 & 2", key="expand_left_pane", use_container_width=True):
            st.session_state.left_pane_collapsed = False
            st.rerun()
    else:
        if st.button(
            "\u25c0 Minimize Stage 1 & 2",
            help="Collapse this panel to give the YAML view/edit panel more room",
            key="collapse_left_pane",
        ):
            st.session_state.left_pane_collapsed = True
            st.rerun()
        _render_stage_1_and_2()

with yaml_col:
    _render_yaml_panel(yaml_panel_height)


