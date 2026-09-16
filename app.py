"""Streamlit app: an Apache Ossie (Open Semantic Interchange) semantic model
builder.

Layout
------
- Sidebar (collapsible): model name/description settings, a section
  selector (Base Model, Enrich Base Model, BI Conversions, AI Agent
  Invocation -- placeholder), sample data, template downloads, and reset.
  Everything you configure or navigate with lives in one place here.
- Main area, center column: renders whichever section is currently
  selected in the sidebar.
- Main area, right column ("Ossie" section): a persistent, always-visible
  YAML view/edit panel, shared by every section -- whatever the Base Model
  section generates, or the Enrich section enriches, shows up here
  immediately. It can be expanded to full screen with the \u00ab / \u00bb
  toggle (which hides the center column so the YAML can take up almost the
  full page width).

There is no separate "database.schema" or "dialect" setting anywhere in
this UI: the dataset ``source`` is inferred entirely from the metadata
file's qualified ``Name`` column, and each metric's SQL dialect comes from
its own ``Dialect`` column in the metrics file (see ``ossie_builder.py``).

Spec reference: https://github.com/apache/ossie/tree/main/core-spec
"""

import os
import traceback
from typing import Any, Dict, List, Tuple

import streamlit as st
import yaml
from dotenv import load_dotenv
from streamlit_tree_select import tree_select

import fabric_deploy as fd
import git_registry as gitreg
import ossie_builder as ob
import powerbi_export as pbe

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT_DIR, ".env"))  # populates GIT_REGISTRY_TOKEN etc. if present; no-op otherwise

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

SECTIONS = [
    ("base_model", "\U0001f4e6 Base Model"),
    ("enrich", "\U0001f9e9 Enrich Base Model"),
    ("bi", "\U0001f504 BI Conversions"),
    ("ai_agent", "\U0001f916 AI Agent Invocation"),
]

# Only these two sections save/load against the model registry -- Base
# Model writes/reads basemodel/, Enrich Base Model writes/reads AIEnrich/.
REGISTRY_DIR_BY_SECTION = {
    "base_model": gitreg.BASE_MODEL_DIR,
    "enrich": gitreg.AI_ENRICH_DIR,
}

st.set_page_config(
    page_title="Ossie Semantic Model Builder",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _build_theme_css(dark: bool) -> str:
    """Returns the full <style> block for either theme. Streamlit's own
    base theme (.streamlit/config.toml) is fixed at server start, so "dark
    mode" here is a thorough CSS override of the elements we can safely
    target (app/sidebar backgrounds, cards, text, form controls, buttons)
    rather than a native Streamlit theme switch -- a few deeply-native
    widget internals may not repaint perfectly, but everything visible and
    commonly used is covered.
    """
    if dark:
        app_bg = (
            "radial-gradient(circle at 8% 0%, rgba(108,92,231,0.30) 0%, transparent 45%),"
            "radial-gradient(circle at 100% 15%, rgba(253,121,198,0.20) 0%, transparent 40%),"
            "radial-gradient(circle at 15% 100%, rgba(80,160,255,0.18) 0%, transparent 45%),"
            "linear-gradient(160deg, #14121F 0%, #1B1730 50%, #1F1526 100%)"
        )
        sidebar_bg = "linear-gradient(180deg, #1C1830 0%, #241A38 100%)"
        sidebar_border = "#3A3155"
        hero_bg = "linear-gradient(135deg, #4B3FA6 0%, #6C5CE7 55%, #C2418F 130%)"
        hero_shadow = "0 8px 28px rgba(0,0,0,0.45)"
        card_bg = "#221D33"
        card_border = "#3A3155"
        card_shadow = "0 2px 10px rgba(0,0,0,0.35)"
        text_color = "#EDE9F7"
        input_bg = "#1A1626"
        metric_accent = "#A29BFE"
        secondary_btn_bg = "#241E38"
    else:
        app_bg = (
            "radial-gradient(circle at 8% 0%, #EAE3FF 0%, transparent 42%),"
            "radial-gradient(circle at 100% 12%, #FFE6F5 0%, transparent 38%),"
            "radial-gradient(circle at 20% 100%, #E1F4FF 0%, transparent 45%),"
            "linear-gradient(160deg, #F7F4FF 0%, #F3EEFF 50%, #FBF0FA 100%)"
        )
        sidebar_bg = "linear-gradient(180deg, #EEE7FD 0%, #E3D9FB 100%)"
        sidebar_border = "#D8CCF8"
        hero_bg = "linear-gradient(135deg, #6C5CE7 0%, #A29BFE 55%, #FD79C6 130%)"
        hero_shadow = "0 8px 24px rgba(108, 92, 231, 0.25)"
        card_bg = "#FFFFFF"
        card_border = "#E4DEFB"
        card_shadow = "0 2px 8px rgba(108, 92, 231, 0.10)"
        text_color = "#1E1B2E"
        input_bg = "#FFFFFF"
        metric_accent = "#6C5CE7"
        secondary_btn_bg = "#FFFFFF"

    return f"""
<style>
/* Hide Streamlit's own hamburger menu, Deploy button, and running-status
   widget -- not useful for this app. Deliberately does NOT hide the
   header/toolbar container itself: that same container is also where the
   sidebar's own "\u00bb" re-expand button (stExpandSidebarButton) lives
   once the sidebar is collapsed, so hiding the whole thing would strand
   users with no way to bring the sidebar back. */
header[data-testid="stHeader"] {{
    background: transparent;
    box-shadow: none;
}}
div[data-testid="stToolbar"] {{
    background: transparent;
}}
[data-testid="stMainMenu"], [data-testid="stAppDeployButton"], [data-testid="stStatusWidget"] {{
    display: none;
}}
.stApp {{
    background: {app_bg};
    background-attachment: fixed;
}}
[data-testid="stSidebar"] {{
    background: {sidebar_bg};
}}
[data-testid="stSidebar"] > div:first-child {{
    border-right: 1px solid {sidebar_border};
}}

.ossie-hero {{
    background: {hero_bg};
    padding: 1.6rem 2rem;
    border-radius: 16px;
    color: white;
    margin-bottom: 1.3rem;
    box-shadow: {hero_shadow};
}}
.ossie-hero h1 {{ margin: 0; font-size: 1.85rem; }}
.ossie-hero p {{ margin: 0.45rem 0 0 0; opacity: 0.96; font-size: 1.02rem; line-height: 1.5; }}
.ossie-hero code {{ background: rgba(255,255,255,0.2); color: white; padding: 0.05rem 0.35rem; border-radius: 4px; }}

/* "Card" surfaces so content pops against the tinted background */
div[data-testid="stExpander"] {{
    background-color: {card_bg};
    border: 1px solid {card_border};
    border-radius: 12px;
    box-shadow: {card_shadow};
}}
div[data-testid="stExpander"] summary {{
    font-weight: 600;
    color: {text_color};
}}
button[kind="primary"] {{
    border-radius: 8px;
    font-weight: 600;
    box-shadow: 0 2px 8px rgba(108, 92, 231, 0.40);
}}
button[kind="secondary"] {{
    border-radius: 8px;
    background-color: {secondary_btn_bg};
    border: 1px solid {card_border};
}}
div[data-testid="stMetric"] {{
    background-color: {card_bg};
    border-radius: 10px;
    padding: 0.6rem 0.8rem 0.3rem 0.8rem;
    border: 1px solid {card_border};
    border-left: 4px solid {metric_accent};
    box-shadow: {card_shadow};
}}
div[data-testid="stTextArea"] textarea {{
    background-color: {input_bg};
    color: {text_color};
    border-radius: 10px;
    border: 1px solid {card_border};
}}
div[data-testid="stTextInput"] input {{
    background-color: {input_bg};
    color: {text_color};
    border: 1px solid {card_border};
}}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    background-color: {input_bg};
    border-color: {card_border};
}}
section[data-testid="stFileUploaderDropzone"] {{
    background-color: {input_bg};
    border: 1px solid {card_border};
}}
div[data-testid="stDataFrame"], div[data-testid="stTable"] {{
    background-color: {card_bg};
    border-radius: 10px;
    overflow: hidden;
}}
div[data-testid="stTabs"] button[role="tab"] {{
    border-radius: 8px 8px 0 0;
}}
[data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"], [data-testid="stWidgetLabel"] {{
    color: {text_color};
}}
hr {{ margin: 0.6rem 0; border-color: {card_border}; }}
</style>
"""


st.session_state.setdefault("dark_mode", False)
st.markdown(_build_theme_css(st.session_state.dark_mode), unsafe_allow_html=True)


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


def _current_model_name() -> str:
    return (st.session_state.get("model_name_input") or "").strip() or "semantic_model"


def _current_model_description() -> str:
    return (st.session_state.get("model_description_input") or "").strip()


def _dataset_field_counts(model: Dict[str, Any]):
    entry = model["semantic_model"][0]
    n_datasets = len(entry.get("datasets", []))
    n_fields = sum(len(d.get("fields", [])) for d in entry.get("datasets", []))
    n_rels = len(entry.get("relationships", []))
    n_metrics = len(entry.get("metrics", []))
    return n_datasets, n_fields, n_rels, n_metrics


def _show_validation(errors: List[str]) -> None:
    if errors:
        st.error("This YAML does NOT pass validation against the Ossie JSON Schema:")
        for err in errors:
            st.code(err, language="text")
    else:
        st.success("\u2705 Valid against the Apache Ossie core-spec JSON Schema.")


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
        filtered[name] = ob.TableMeta(
            table_name=table.table_name, asset_type=table.asset_type, source=table.source, columns=kept_cols
        )
    return filtered, dropped


# ---------------------------------------------------------------------------
# Tree-style preview & selection widgets
# ---------------------------------------------------------------------------

_TREE_LEAF_SEP = "::"


def _leaf_value(table_name: str, column_name: str) -> str:
    return f"{table_name}{_TREE_LEAF_SEP}{column_name}"


def _build_metadata_tree_nodes(tables: Dict[str, "ob.TableMeta"]) -> List[Dict[str, Any]]:
    nodes = []
    for table_name, table in tables.items():
        children = []
        for c in table.columns:
            dt = ob.map_technical_datatype(c.technical_data_type)
            badges = []
            if c.is_primary_key:
                badges.append("\U0001f511")  # key
            if c.contains_pii:
                badges.append("\U0001f512")  # lock
            badge_str = (" " + " ".join(badges)) if badges else ""
            label = f"\U0001f4c4 {c.column_name}  \u00b7  {dt}{badge_str}"
            children.append({"value": _leaf_value(table_name, c.column_name), "label": label})
        table_label = (
            f"\U0001f4c1 {table_name}  \u2014  {table.asset_type or 'Table'}  "
            f"({len(table.columns)} columns)"
        )
        nodes.append({"value": table_name, "label": table_label, "children": children})
    return nodes


def _render_metadata_tree(tables: Dict[str, "ob.TableMeta"]) -> Dict[Tuple[str, str], bool]:
    """A real folder/checkbox tree (react-checkbox-tree via
    streamlit-tree-select): tables are folders, columns are leaves, and
    parent checkboxes show a tri-state (checked / unchecked / indeterminate)
    reflecting how many of their columns are currently selected -- clicking
    a table's own checkbox toggles every column underneath it at once.
    """
    nodes = _build_metadata_tree_nodes(tables)
    all_leaf_values = [_leaf_value(t, c.column_name) for t, tbl in tables.items() for c in tbl.columns]
    valid_leaf_set = set(all_leaf_values)
    valid_table_set = set(tables.keys())

    tree_key = "metadata_tree"
    prior = st.session_state.get(tree_key)
    if prior:
        prior_checked = [v for v in prior.get("checked", []) if v in valid_leaf_set]
        default_checked = prior_checked if prior_checked else all_leaf_values
        default_expanded = [v for v in prior.get("expanded", []) if v in valid_table_set]
    else:
        default_checked = all_leaf_values
        default_expanded = []

    st.caption(
        "Tables are folders, columns are leaves. Uncheck a column to exclude it, or uncheck a "
        "whole table's checkbox to exclude it entirely. Tables collapse by default to keep this "
        "compact -- use **Expand all** below to review everything at once."
    )
    result = tree_select(
        nodes,
        check_model="leaf",
        checked=default_checked,
        expanded=default_expanded,
        show_expand_all=True,
        key=tree_key,
    )
    checked_set = set(result.get("checked", default_checked))

    selection: Dict[Tuple[str, str], bool] = {}
    for table_name, table in tables.items():
        for c in table.columns:
            selection[(table_name, c.column_name)] = _leaf_value(table_name, c.column_name) in checked_set
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
                dialect_badge = f"  :blue-badge[{m.get('dialect', 'ANSI_SQL')}]"
                st.markdown(f"`{m['name']}`{dt_badge}{dialect_badge}")
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
# Right pane -- the persistent "Ossie" YAML view/edit panel
# ---------------------------------------------------------------------------

def _render_yaml_panel(height: int, fullscreen: bool):
    """Always-visible YAML view/edit panel, shared by every left-pane
    section. Includes the \u00ab / \u00bb full-screen toggle and the
    "Download YAML" (registry) action.
    """
    header_cols = st.columns([4, 2])
    with header_cols[0]:
        st.header("\U0001f4c4 Ossie YAML")
    with header_cols[1]:
        toggle_label = "\u00ab Exit full screen" if fullscreen else "Full screen \u00bb"
        toggle_help = "Exit full screen" if fullscreen else "Expand to full screen"
        if st.button(toggle_label, key="yaml_fullscreen_toggle", help=toggle_help, use_container_width=True):
            st.session_state.yaml_fullscreen = not fullscreen
            st.rerun()

    if st.session_state.model is None:
        st.info(
            "Your generated YAML will appear here once you run **Generate base YAML** in the "
            "**Base Model** tab. This panel stays open across every section so you can view and "
            "edit it at any time. Already have a model saved in the registry? Load it below "
            "instead of building a new one.",
            icon="\U0001f4dd",
        )
        _render_registry_section()
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

        c1, c2, c3 = st.columns(3)
        with c1:
            apply_edits = st.button("\u2705 Apply edits", use_container_width=True)
        with c2:
            validate_clicked = st.button("\U0001f50d Validate YAML", use_container_width=True)
        with c3:
            st.download_button(
                label="\U0001f4be Download YAML",
                data=st.session_state.yaml_editor,
                file_name=f"{_current_model_name()}.ossie.yaml",
                mime="application/x-yaml",
                use_container_width=True,
                type="primary",
                key="registry_download_yaml",
            )

        if apply_edits:
            try:
                parsed = ob.parse_yaml_text(st.session_state.yaml_editor)
                st.session_state.model = parsed
                st.session_state.validation_result = None
                st.success("Edits applied.")
            except (yaml.YAMLError, ValueError) as e:
                st.error(f"Could not parse your edits as valid YAML: {e}")

        if validate_clicked:
            try:
                candidate = ob.parse_yaml_text(st.session_state.yaml_editor)
                st.session_state.validation_result = ob.validate_model(candidate, SCHEMA_PATH)
            except (yaml.YAMLError, ValueError) as e:
                st.session_state.validation_result = [f"Can't validate -- not parseable YAML: {e}"]

        if st.session_state.validation_result is not None:
            _show_validation(st.session_state.validation_result)
        _render_registry_section()


def _render_registry_section():
    """Save/load the current YAML to/from the GitHub-backed model registry
    (see git_registry.py). Only shown for the Base Model and Enrich Base
    Model sections -- each saves/loads its own directory in the registry
    repo (basemodel/ and AIEnrich/ respectively). The model name field
    (sidebar) determines the saved file name; saving always overwrites
    that file ("last write wins") -- git's own commit history on the
    registry repo is the version log.

    **Load is available even before any model has been generated** -- you
    don't need to build a new base model first just to open and edit one
    that's already saved in the registry. Save, naturally, only makes
    sense once a model exists (there'd otherwise be nothing to save).
    """
    registry_dir = REGISTRY_DIR_BY_SECTION.get(st.session_state.active_section)
    if registry_dir is None:
        return

    model_loaded = st.session_state.model is not None

    st.divider()
    st.markdown(
        f"**\U0001f4da Registry** \u2014 `{registry_dir}/` in "
        f"[`{gitreg.DEFAULT_OWNER}/{gitreg.DEFAULT_REPO}`](https://github.com/{gitreg.DEFAULT_OWNER}/{gitreg.DEFAULT_REPO})"
    )

    if st.session_state.registry_message:
        kind, text = st.session_state.registry_message
        (st.success if kind == "success" else st.error)(text)
        st.session_state.registry_message = None

    token = gitreg.get_registry_token()
    if not token:
        st.caption(
            "Not configured. Copy `.env.example` to `.env` and set `GIT_REGISTRY_TOKEN` (a "
            "GitHub personal access token with write access to the registry repo) to enable "
            "saving/loading models here."
        )
        return

    if model_loaded:
        filename = gitreg.safe_model_filename(_current_model_name())
        save_cols = st.columns([2, 1])
        with save_cols[0]:
            commit_message = st.text_input(
                "Save notes",
                key=f"registry_commit_msg_{st.session_state.active_section}",
                label_visibility="collapsed",
                placeholder="Save notes (becomes the git commit message)",
            )
        with save_cols[1]:
            if st.button(f"\U0001f4be Save to {registry_dir}/", use_container_width=True, key=f"registry_save_{st.session_state.active_section}"):
                result = gitreg.save_model(
                    registry_dir, filename, st.session_state.yaml_editor,
                    commit_message or f"Save {filename}", token,
                )
                if result.success:
                    msg = f"\u2705 Saved `{filename}` to `{registry_dir}/`."
                    if result.commit_url:
                        msg += f" [View commit]({result.commit_url})"
                    st.session_state.registry_message = ("success", msg)
                else:
                    st.session_state.registry_message = ("error", f"\u274c {result.message}")
                st.rerun()
    else:
        st.caption(
            "Generate or edit a model first to save one here \u2014 but you can **load** an "
            "already-saved model below right now, with no new model needed first."
        )

    with st.expander(f"\U0001f4c2 Load from {registry_dir}/", expanded=not model_loaded):
        list_result = gitreg.list_models(registry_dir, token)
        if not list_result.success:
            st.error(f"Failed to list saved models: {list_result.message}")
        elif not list_result.files:
            st.caption("No models saved here yet.")
        else:
            display_names = [gitreg.display_name_from_filename(f) for f in list_result.files]
            chosen = st.selectbox(
                "Model", display_names, key=f"registry_load_choice_{st.session_state.active_section}"
            )
            if st.button("\U0001f4e5 Load selected model", key=f"registry_load_btn_{st.session_state.active_section}"):
                chosen_filename = list_result.files[display_names.index(chosen)]
                load_result = gitreg.load_model(registry_dir, chosen_filename, token)
                if not load_result.success:
                    st.session_state.registry_message = ("error", f"\u274c {load_result.message}")
                    st.rerun()
                    return
                try:
                    parsed = ob.parse_yaml_text(load_result.content)
                except (yaml.YAMLError, ValueError) as e:
                    st.session_state.registry_message = ("error", f"\u274c Loaded file isn't valid YAML: {e}")
                    st.rerun()
                    return
                # Can't touch st.session_state.yaml_editor or model_name_input
                # directly here -- those widgets were already instantiated
                # earlier in this run. Stash and apply them on the rerun.
                st.session_state["_pending_model"] = parsed
                # Reflect the name of the file the user actually picked in
                # the dropdown -- NOT whatever "name" happens to be baked
                # into the YAML body, which can drift out of sync with the
                # registry filename if the sidebar's Model name field was
                # edited after generating but before saving. `chosen` is
                # always non-empty here (it came from the dropdown itself).
                st.session_state["_pending_model_name"] = chosen
                st.session_state.registry_message = ("success", f"\u2705 Loaded '{chosen}' from `{registry_dir}/`.")
                st.rerun()


# ---------------------------------------------------------------------------
# Center pane -- Base Model section
# ---------------------------------------------------------------------------

def _render_base_model_section():
    st.badge("BASE MODEL", color="blue")
    st.header("\U0001f4e6 Base Model")
    st.caption(
        "There's no separate database/schema or SQL-dialect setting here: the dataset `source` "
        "is inferred from the metadata file's qualified `Name` column (e.g. "
        "`WEALTH_DB.PUBLIC.FACT_POSITION`), and each metric's dialect comes from its own "
        "`Dialect` column in the metrics file."
    )

    model_already_loaded = st.session_state.model is not None

    st.subheader("1. Upload your files")
    if model_already_loaded:
        st.info(
            "A model is already loaded (generated earlier, hand-edited, or loaded from the "
            "**Registry**). Every upload below is now **optional** \u2014 upload just the "
            "piece(s) you want to add or update (new/changed table metadata, metrics/synonyms/"
            "extensions, and/or relationships); anything you don't re-upload is left exactly as "
            "it is. You can also skip uploading entirely and edit the YAML directly in the "
            "**Ossie** panel on the right.",
            icon="\u2139\ufe0f",
        )
    st.caption(
        "Accepted formats: **.csv, .xlsx, .txt** (delimited). Multi-sheet Excel workbooks are combined automatically."
    )

    metadata_req_tag = "optional \u2014 adds/updates tables" if model_already_loaded else "required"
    upload_cols = st.columns(3)
    with upload_cols[0]:
        st.markdown(f"**\u2460 Table & column metadata** `{metadata_req_tag}`")
        st.caption(
            "One row per column: Name (optionally qualified, e.g. `DB.SCHEMA.TABLE`), Assest "
            "Type, Column Title, Description, Description from source system, Source Column "
            "Name, size, Technical "
            "Data Type, Column Position, Is Primary Key, Is nullable, Contains PII, Primary Key."
        )
        metadata_upload = st.file_uploader(
            "Metadata file", type=["csv", "xlsx", "txt"], key="metadata_upload", label_visibility="collapsed"
        )
    with upload_cols[1]:
        st.markdown("**\u2461 Metrics, synonyms & custom extensions** `optional`")
        st.caption(
            "One row per item, `Type` = Metric / Synonym / Custom Extension: Table Name, Column "
            "Name, Metric Name/Expression/Description/Data Type/**Dialect**, Synonyms, Custom "
            "Extension Vendor/Data."
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
            "Using the bundled **Account / Position** sample data. Click **Clear sample data** "
            "in the sidebar to switch back to your own uploads.",
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
        return

    if metadata_df is None and not model_already_loaded:
        st.warning("Upload the table/column metadata file (or load the sample data) to continue.")
        return

    nothing_new_uploaded = metadata_df is None and enrichment_df is None and relationships_df is None
    if nothing_new_uploaded and model_already_loaded:
        n_datasets, n_fields, n_rels, n_metrics = _dataset_field_counts(st.session_state.model)
        st.caption(
            f"Current loaded model: **{n_datasets}** datasets, **{n_fields}** fields, "
            f"**{n_rels}** relationships, **{n_metrics}** metrics. Upload a file above to add "
            "to it, or edit the YAML directly in the Ossie panel on the right."
        )
        return

    parse_error = None
    try:
        tables = ob.parse_metadata(metadata_df) if metadata_df is not None else None
        enrichment = ob.parse_metrics_synonyms_extensions(enrichment_df)
        relationships = ob.parse_relationships(relationships_df)
    except Exception as e:  # noqa: BLE001
        parse_error = str(e)
        tables, enrichment, relationships = None, None, None

    if parse_error:
        st.error(f"Failed to parse the uploaded files: {parse_error}")
        return

    st.subheader("2. Review & select what to include" if tables is not None else "2. Review what will be added")
    selected_columns: Dict[Tuple[str, str], bool] = {}
    selected_metrics: Dict[str, bool] = {}
    if tables is not None:
        st.caption(
            "Expand each table to uncheck columns you don't want in the YAML. Deselecting every "
            "column in a table drops that table entirely."
        )
        tree_tabs = st.tabs(["\U0001f9f1 Tables & columns", "\U0001f4d0 Metrics", "\U0001f517 Relationships"])
        with tree_tabs[0]:
            selected_columns = _render_metadata_tree(tables)
        with tree_tabs[1]:
            selected_metrics = _render_metrics_tree(enrichment.metrics)
        with tree_tabs[2]:
            _render_relationships_section(relationships, tables)
    else:
        # No new table metadata this round -- just previewing the metrics
        # and/or relationships that will be merged into the already-loaded
        # model (existing tables come from that model, not shown again here).
        tree_tabs = st.tabs(["\U0001f4d0 Metrics", "\U0001f517 Relationships"])
        with tree_tabs[0]:
            selected_metrics = _render_metrics_tree(enrichment.metrics)
        with tree_tabs[1]:
            _render_relationships_section(relationships, {})

    for w in enrichment.warnings:
        st.warning(w)

    button_label = "\U0001f680 Generate base YAML" if not model_already_loaded else "\U0001f504 Apply changes to loaded model"
    st.subheader("3. Generate the base Ossie YAML" if not model_already_loaded else "3. Apply changes")
    generate = st.button(button_label, type="primary")

    if generate:
        try:
            filtered_metrics = [m for m in enrichment.metrics if selected_metrics.get(m["name"], True)]

            if tables is not None:
                filtered_tables, dropped_tables = _filter_tables(tables, selected_columns)
                for name in dropped_tables:
                    st.warning(f"Table '{name}' excluded from the YAML \u2014 no columns were selected.")
            else:
                filtered_tables = None

            if model_already_loaded:
                result = ob.merge_updates_into_model(
                    st.session_state.model,
                    new_tables=filtered_tables,
                    metrics=filtered_metrics,
                    field_synonyms=enrichment.field_synonyms,
                    dataset_extensions=enrichment.dataset_extensions,
                    field_extensions=enrichment.field_extensions,
                    relationships=relationships,
                )
                toast_message = "Changes applied to the loaded model \u2014 see the Ossie panel on the right."
            else:
                result = ob.build_semantic_model(
                    model_name=_current_model_name(),
                    model_description=_current_model_description(),
                    tables=filtered_tables,
                    relationships=relationships,
                    metrics=filtered_metrics,
                    field_synonyms=enrichment.field_synonyms,
                    dataset_extensions=enrichment.dataset_extensions,
                    field_extensions=enrichment.field_extensions,
                )
                toast_message = "Base YAML generated \u2014 see the Ossie panel on the right."

            for w in result.warnings:
                st.warning(w)

            st.session_state.model = result.model
            st.session_state.yaml_editor = ob.to_yaml(result.model)
            st.session_state.validation_result = None
            st.toast(toast_message, icon="\u2705")
            st.rerun()

        except Exception as e:  # noqa: BLE001
            st.error(f"Failed to generate the semantic model: {e}")
            with st.expander("Show details"):
                st.code(traceback.format_exc())


# ---------------------------------------------------------------------------
# Center pane -- Enrich Base Model section (was "Stage 2")
# ---------------------------------------------------------------------------

def _render_enrich_section():
    st.badge("ENRICH BASE MODEL", color="violet")
    st.header("\U0001f9e9 Enrich Base Model")

    if st.session_state.model is None:
        st.caption("Generate a base YAML in the **Base Model** tab first \u2014 this section unlocks once one exists.")
        return

    st.caption(
        "Upload this **after** you already have a base YAML (freshly generated or hand-edited "
        "in the Ossie panel on the right). It never overwrites existing instructions/synonyms/"
        "examples \u2014 it **concatenates** new ones onto whatever a table/field/model already "
        "has, and **appends** any `Custom Extension` value as a new `custom_extensions` entry "
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
                    "AI context merged into the Ossie panel (synonyms/instructions "
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


# ---------------------------------------------------------------------------
# Center pane -- BI Conversions section (was "Stage 3")
# ---------------------------------------------------------------------------

def _render_bi_section():
    st.badge("BI CONVERSIONS", color="orange")
    st.header("\U0001f504 BI Conversions")

    if st.session_state.model is None:
        st.caption("Generate a base YAML in the **Base Model** tab first \u2014 this section unlocks once one exists.")
        return

    st.caption(
        "Convert the current Ossie semantic model into a target BI tool's own semantic model "
        "format, with one click \u2014 no desktop application required to generate it."
    )
    bi_tabs = st.tabs(["\U0001f7e6 Power BI", "\U0001f4ca Tableau"])

    with bi_tabs[0]:
        st.markdown(
            "Generates a real **Power BI Project**: a top-level `.pbip` file, a `.Report` "
            "folder (a minimal blank report), and a `.SemanticModel` folder (TMSL `model.bim`, "
            "plain JSON \u2014 never mixed with a TMDL folder, which Power BI Desktop treats as "
            "invalid), all zipped together \u2014 unzip and open the `.pbip` file in Power BI Desktop "
            "(*File \u2192 Open \u2192 Power BI Project*) with **no other tool required**. The "
            "blank report is a best-effort scaffold (built without a real Power BI Desktop "
            "available here to test against) \u2014 if it doesn't open cleanly in yours, the "
            "`.SemanticModel` folder still works on its own via **Tabular Editor** (deploy "
            "`model.bim` to a blank Desktop file or a Premium/Fabric XMLA endpoint), the "
            "**Deploy to Fabric** tab below, or git integration \u2014 see the README for all "
            "of these paths."
        )

        use_snowflake = st.checkbox("\U0001f9ca Use Snowflake as the data source", key="pbi_use_snowflake")
        if use_snowflake:
            st.caption(
                "Fill this in to generate real `Snowflake.Databases(...)` M code \u2014 Power "
                "BI's native Snowflake connector syntax, ready to connect. Database/schema/table "
                "come from each dataset's Ossie `source` field, which is inferred from the "
                "metadata file's qualified `Name` column (e.g. `MY_DB.PUBLIC.MY_TABLE`). **No "
                "credentials are entered or stored here** \u2014 Power BI prompts for those "
                "(username/password, SSO, key-pair, ...) the first time the model connects or "
                "refreshes."
            )
        else:
            st.caption(
                "Without Snowflake configured, each table's Power Query source is a generic "
                "placeholder you'd hand-edit later."
            )

        # All of the Snowflake fields (when shown) and the conversion trigger live inside one
        # form so their values are submitted together, atomically, in a single event -- a
        # plain button here could race with a text_input's value not yet having committed to
        # session_state if the button is clicked immediately after typing.
        with st.form("pbi_convert_form"):
            sf_account = sf_warehouse = sf_role = ""
            if use_snowflake:
                sf_cols = st.columns(3)
                with sf_cols[0]:
                    sf_account = st.text_input(
                        "Account URL", placeholder="myorg-myaccount.snowflakecomputing.com", key="pbi_sf_account"
                    )
                with sf_cols[1]:
                    sf_warehouse = st.text_input("Warehouse", placeholder="COMPUTE_WH", key="pbi_sf_warehouse")
                with sf_cols[2]:
                    sf_role = st.text_input("Role (optional)", key="pbi_sf_role")
            convert_clicked = st.form_submit_button(
                "\U0001f504 Convert to Power BI semantic model", type="primary"
            )

        if convert_clicked:
            data_source = None
            if use_snowflake and sf_account.strip() and sf_warehouse.strip():
                data_source = {
                    "type": "snowflake",
                    "account": sf_account.strip(),
                    "warehouse": sf_warehouse.strip(),
                    "role": sf_role.strip() or None,
                }
            elif use_snowflake:
                st.warning("Enter at least the Account URL and Warehouse to generate Snowflake M code \u2014 falling back to the generic placeholder for now.")

            try:
                st.session_state["pbi_export"] = pbe.convert_to_powerbi(
                    st.session_state.model, _current_model_name(), data_source=data_source
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
                file_name=f"{_current_model_name()}.SemanticModel.zip",
                mime="application/zip",
                type="primary",
                key="dl_pbi_zip",
            )
            if "Snowflake.Databases" in export.tmsl_json:
                st.caption(
                    "\u2705 This export uses real Snowflake connection code. Next: unzip it and "
                    "open the `.pbip` file in Power BI Desktop, then hit **Refresh** \u2014 Power "
                    "BI will prompt you for Snowflake sign-in (username/password, SSO, or "
                    "key-pair) and pull real data into the measures above. If the `.pbip` "
                    "doesn't open cleanly, fall back to Tabular Editor with `model.bim` (see "
                    "the README)."
                )
            else:
                st.caption(
                    "This export uses a generic placeholder data source. Check **Use Snowflake "
                    "as the data source** above, fill in your account/warehouse, and re-convert "
                    "to get real, ready-to-connect Snowflake M code instead."
                )

            pbi_view_tabs = st.tabs(
                [
                    "model.bim (TMSL)",
                    "TMDL files",
                    "\u25b6\ufe0f Preview metrics (DAX)",
                    "\U0001f6f0\ufe0f Deploy to Fabric",
                ]
            )
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

            with pbi_view_tabs[3]:
                st.markdown(
                    "Push this semantic model straight into a **Microsoft Fabric workspace** "
                    "over HTTPS \u2014 skipping Power BI Desktop, SSMS, and Tabular Editor "
                    "entirely. This calls the real "
                    "[Fabric REST API](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model) "
                    "(`POST /v1/workspaces/{id}/semanticModels`)."
                )
                st.caption(
                    "**Requires** (outside this app, on your Microsoft tenant): a "
                    "**Fabric-enabled workspace** (Fabric trial capacity, Premium, or PPU \u2014 "
                    "plain Power BI Pro workspaces don't support this API) and a **bearer "
                    "token** for it. Get a token from any terminal, no desktop app:"
                )
                st.code(
                    "az login\n"
                    "az account get-access-token --resource https://api.fabric.microsoft.com "
                    "--query accessToken -o tsv",
                    language="bash",
                )
                st.caption(
                    "The token is used only for this one request and is never written to "
                    "disk or logged. Tokens expire after about an hour."
                )

                with st.form("fabric_deploy_form"):
                    fcols = st.columns(2)
                    with fcols[0]:
                        fabric_workspace_id = st.text_input(
                            "Fabric workspace ID (GUID)",
                            placeholder="e.g. cfafbeb1-8037-4d0c-896e-a46fb27ff229",
                            key="fabric_workspace_id",
                        )
                    with fcols[1]:
                        fabric_display_name = st.text_input(
                            "Semantic model name in Fabric",
                            value=export.display_name,
                            key="fabric_display_name",
                        )
                    fabric_token = st.text_input(
                        "Bearer token",
                        type="password",
                        key="fabric_bearer_token",
                        help="Pasted here only for this request; not stored or logged.",
                    )
                    deploy_clicked = st.form_submit_button(
                        "\U0001f680 Deploy to Fabric workspace", type="primary"
                    )

                if deploy_clicked:
                    with st.spinner("Deploying to Fabric \u2014 this can take up to a couple of minutes..."):
                        st.session_state["fabric_deploy_result"] = fd.create_semantic_model(
                            workspace_id=fabric_workspace_id,
                            bearer_token=fabric_token,
                            display_name=fabric_display_name.strip() or export.display_name,
                            tmdl_files=export.tmdl_files,
                            pbism_bytes=export.pbism_bytes,
                            platform_bytes=export.platform_bytes,
                            description="Generated by the Ossie Semantic Model Builder from an Apache Ossie YAML.",
                        )

                deploy_result = st.session_state.get("fabric_deploy_result")
                if deploy_result is not None:
                    if deploy_result.success:
                        st.success(f"\u2705 {deploy_result.message}")
                        if deploy_result.workspace_url:
                            st.markdown(f"[Open the workspace \u2192]({deploy_result.workspace_url})")
                    else:
                        st.error(f"\u274c {deploy_result.message}")

                st.caption(
                    "No Fabric workspace handy, or don't want to hand over a token? Commit the "
                    "downloaded `.SemanticModel` folder to a git repo connected to a Fabric "
                    "workspace's **git integration** instead \u2014 `git push` alone syncs it, "
                    "with no API call and no desktop app either."
                )

    with bi_tabs[1]:
        st.info(
            "**Tableau data model export isn't built in this app yet** \u2014 only the Power BI "
            "path is implemented for now. The same Ossie YAML could similarly drive a Tableau "
            "data source (`.tds`/`.tdsx` XML: tables, joins, and calculated fields translated "
            "into Tableau's calculation language) as a future addition.",
            icon="\U0001f6a7",
        )


# ---------------------------------------------------------------------------
# Center pane -- AI Agent Invocation section (placeholder)
# ---------------------------------------------------------------------------

def _render_ai_agent_section():
    st.badge("AI AGENT INVOCATION", color="gray")
    st.header("\U0001f916 AI Agent Invocation")
    st.info(
        "**Placeholder.** This section will host AI agent invocation against the current Ossie "
        "semantic model (e.g. answering natural-language questions using its metrics, "
        "relationships, and `ai_context`). Agent code will be added here in a future update.",
        icon="\U0001f916",
    )
    if st.session_state.model is None:
        st.caption("Generate a base YAML in the **Base Model** tab first.")
        return
    st.text_input(
        "Ask a question about this model",
        placeholder="e.g. What was the total market value last month?",
        disabled=True,
        key="ai_agent_placeholder_input",
    )


# ---------------------------------------------------------------------------
# Center pane -- About expander (always available, any section)
# ---------------------------------------------------------------------------

def _render_about_expander():
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

**No global database/schema or dialect setting.** Each dataset's `source`
is inferred from the metadata file's qualified `Name` column (e.g.
`WEALTH_DB.PUBLIC.FACT_POSITION` \u2192 dataset name `FACT_POSITION`,
source `WEALTH_DB.PUBLIC.FACT_POSITION`); each metric's SQL dialect comes
from its own `Dialect` column in the metrics file (defaulting to
`ANSI_SQL`). Field expressions are always `ANSI_SQL` (they're just plain
column references).

**Sections**

1. **Base Model**: Table/column metadata is required. The metrics/
   synonyms/custom-extensions file and the relationships file are both
   optional. Use the tree view to uncheck columns/metrics you don't want
   before generating -- deselected columns (and tables left with zero
   selected columns) are excluded from the YAML.
2. **Enrich Base Model**: uploaded later, merges into whatever base YAML
   currently exists (freshly generated *or* hand-edited in the Ossie
   panel). It only **adds**.
3. **BI Conversions**: converts the current model into a target BI tool's
   own semantic model format. Only **Power BI** is implemented -- it
   produces real TMSL (`model.bim`) and a TMDL-based Power BI Project,
   deployable via Power BI Desktop or headlessly via the Fabric REST API,
   with a local synthetic-data preview of the DAX measures. Tableau shows
   as a planned option, not yet built.
4. **AI Agent Invocation**: placeholder for a future AI agent integration.

**Mapping summary**

- **Name** (optionally `DB.SCHEMA.TABLE`) &rarr; dataset name (last
  segment) + `source` (verbatim)
- **Column Title** &rarr; field name &middot; **Description** &rarr; field `description`
- **Description from source system**, **Source Column Name** (optional physical
  Snowflake column for Power BI binding), **size**, **Technical Data Type**,
  **Column Position**, **Is nullable**, **Contains PII** &rarr; field
  `custom_extensions` (vendor `COMMON`)
- **Technical Data Type** is also best-effort mapped to the Ossie
  `datatype` enum
- **Is Primary Key** (+ **Primary Key** group label) &rarr; dataset
  `primary_key` (supports composite keys)
- **Assest Type**, PII column roll-up &rarr; dataset `custom_extensions`
- File 2 `Metric` rows (+ their own **Dialect**) &rarr; native `semantic_model[].metrics[]`
- File 2 `Synonym` rows &rarr; field `ai_context.synonyms` (base synonyms)
- File 2 `Custom Extension` rows &rarr; `custom_extensions` on the dataset
  or field
- Relationships file &rarr; native `semantic_model[].relationships[]`
  (also visualized as a diagram)
- File 4 AI context (Enrich Base Model) &rarr; concatenated into
  `ai_context` + appended into `custom_extensions` (`vendor_name: AI_ENRICHMENT`)
            """
        )


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

st.session_state.setdefault("use_sample_base", False)
st.session_state.setdefault("use_sample_ai_context", False)
st.session_state.setdefault("model", None)
st.session_state.setdefault("yaml_editor", "")
st.session_state.setdefault("enrichment_message", None)
st.session_state.setdefault("yaml_fullscreen", False)
st.session_state.setdefault("yaml_panel_tall", False)
st.session_state.setdefault("pbi_export", None)
st.session_state.setdefault("pbi_metric_preview", None)
st.session_state.setdefault("fabric_deploy_result", None)
st.session_state.setdefault("active_section", SECTIONS[0][0])
st.session_state.setdefault("registry_message", None)
st.session_state.setdefault("validation_result", None)

# A widget's session_state value can only be set BEFORE that widget is
# instantiated in a given script run. The Enrich section computes its
# result after the "yaml_editor" text_area has already been created, so it
# stashes the new model here and triggers a rerun; this block -- which
# always runs before the text_area is (re)created -- is what actually
# applies it.
if st.session_state.get("_pending_model") is not None:
    st.session_state.model = st.session_state.pop("_pending_model")
    st.session_state.yaml_editor = ob.to_yaml(st.session_state.model)
    st.session_state.validation_result = None

# Same "can't touch a widget's state after it's instantiated" constraint
# applies to the "Model name" sidebar field -- loading from the registry
# stashes the loaded model's own name here so it shows up there too.
if st.session_state.get("_pending_model_name") is not None:
    st.session_state["model_name_input"] = st.session_state.pop("_pending_model_name")

# ---------------------------------------------------------------------------
# Sidebar: model settings, section navigation, sample data, templates, reset
# -- everything you configure/navigate with lives in one collapsible place.
# ---------------------------------------------------------------------------

with st.sidebar:
    _header_cols = st.columns([4, 1])
    with _header_cols[0]:
        st.header("\u2699\ufe0f Model settings")
    with _header_cols[1]:
        st.toggle("\U0001f319", key="dark_mode", help="Toggle dark/light theme")

    st.text_input("Model name", value="account_position_model", key="model_name_input")
    st.text_area(
        "Model description",
        value="Investment account and position semantic model covering client, "
        "account, security, and calendar dimensions with a daily position fact.",
        height=90,
        key="model_description_input",
    )

    st.divider()
    st.header("\U0001f4cb Sections")
    _current_section_key = st.session_state.get("active_section", SECTIONS[0][0])
    for _section_key, _section_label in SECTIONS:
        _is_active = _current_section_key == _section_key
        if st.button(
            _section_label,
            key=f"section_btn_{_section_key}",
            use_container_width=True,
            type="primary" if _is_active else "secondary",
        ):
            st.session_state.active_section = _section_key
            st.rerun()

    st.divider()
    st.header("\U0001f9ea Try it with sample data")
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
            if k.startswith(("metricsel__", "selall__", "selnone__")) or k == "metadata_tree":
                del st.session_state[k]
        st.session_state.model = None
        st.session_state.yaml_editor = ""
        st.session_state.validation_result = None
        st.session_state.use_sample_base = False
        st.session_state.use_sample_ai_context = False
        st.session_state.pbi_export = None
        st.session_state.pbi_metric_preview = None
        st.session_state.fabric_deploy_result = None
        st.session_state.yaml_fullscreen = False
        st.session_state.registry_message = None
        st.rerun()

# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------

st.markdown(
    """
<div class="ossie-hero">
  <h1>🧬 Apache Ossie Semantic Model Builder</h1>
  <p>Pick a section in the sidebar -- <b>Base Model</b>, <b>Enrich Base Model</b>, <b>BI
  Conversions</b>, or <b>AI Agent Invocation</b> -- and its details appear on the left.
  The <b>Ossie</b> panel on the right always shows the current YAML, live, with a
  <code>\u00ab</code>/<code>\u00bb</code> toggle to expand it to full screen.</p>
</div>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Layout: center (active section) | right (Ossie YAML). Model settings and
# section navigation live in the sidebar (collapsible for more screen room).
# ---------------------------------------------------------------------------

if st.session_state.yaml_fullscreen:
    _render_yaml_panel(height=1300, fullscreen=True)
else:
    center_col, yaml_col = st.columns([1.7, 1], gap="large")

    with center_col:
        active_section = st.session_state.active_section
        if active_section == "base_model":
            _render_base_model_section()
        elif active_section == "enrich":
            _render_enrich_section()
        elif active_section == "bi":
            _render_bi_section()
        elif active_section == "ai_agent":
            _render_ai_agent_section()
        _render_about_expander()

    with yaml_col:
        yaml_panel_height = 1150 if st.session_state.yaml_panel_tall else 650
        _render_yaml_panel(height=yaml_panel_height, fullscreen=False)
