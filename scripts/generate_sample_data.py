"""Generates the sample input files under ``sample_data/`` that demonstrate
the expected upload formats using an investment "Account / Position" fact
and dimensions data model:

    FACT_POSITION  (fact)       -- daily security holdings per account
    DIM_ACCOUNT    (dimension)  -- investment accounts
    DIM_CLIENT     (dimension)  -- clients/households who own accounts
    DIM_SECURITY   (dimension)  -- tradable securities/instruments
    DIM_DATE       (dimension)  -- calendar date dimension

Run with: python3 scripts/generate_sample_data.py
"""

import csv
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")

METADATA_HEADERS = [
    "Name",
    "Assest Type",
    "Column Title",
    "Description",
    "Description from source system",
    "Source Column Name",
    "size",
    "Technical Data Type",
    "Column Position",
    "Is Primary Key",
    "Is nullable",
    "Contains PII",
    "Primary Key",
]

# (table, asset_type, column, description, source_description, source_column_name, size, tech_type,
#  position, is_pk, is_nullable, contains_pii, pk_name)
METADATA_ROWS = [
    # ---- DIM_CLIENT -------------------------------------------
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'CLIENT_ID', 'Unique surrogate identifier for a client or household.',
     'Documented in the source warehouse as column CLNT_SK.', 'CLNT_SK', '10', 'NUMBER(10)', 1, 'Y', 'N', 'N', 'PK_DIM_CLIENT'),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'CLIENT_NAME', 'Legal name of the client or household.',
     'Documented in the source warehouse as column CLNT_NM.', 'CLNT_NM', '200', 'VARCHAR2(200)', 2, 'N', 'N', 'Y', ''),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'CLIENT_TYPE', 'Classification of the client, e.g. Individual or Institutional.',
     'Documented in the source warehouse as column CLNT_TYP_CD.', 'CLNT_TYP_CD', '20', 'VARCHAR2(20)', 3, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'TAX_ID', 'Government tax identifier (e.g. SSN/EIN) associated with the client.',
     'Documented in the source warehouse as column TAX_ID_NBR.', 'TAX_ID_NBR', '20', 'VARCHAR2(20)', 4, 'N', 'Y', 'Y', ''),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'ONBOARDING_DATE', 'Date the client relationship was established.',
     'Documented in the source warehouse as column ONBRD_DT.', 'ONBRD_DT', '', 'DATE', 5, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'RISK_PROFILE', "Client's stated investment risk tolerance.",
     'Documented in the source warehouse as column RISK_PRFL_CD.', 'RISK_PRFL_CD', '20', 'VARCHAR2(20)', 6, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_CLIENT', 'Dimension Table', 'COUNTRY_CODE', "ISO country code of the client's residence.",
     'Documented in the source warehouse as column CNTRY_CD.', 'CNTRY_CD', '3', 'VARCHAR2(3)', 7, 'N', 'N', 'N', ''),
    # ---- DIM_ACCOUNT ------------------------------------------
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'ACCOUNT_ID', 'Unique surrogate identifier for an investment account.',
     'Documented in the source warehouse as column ACCT_SK.', 'ACCT_SK', '10', 'NUMBER(10)', 1, 'Y', 'N', 'N', 'PK_DIM_ACCOUNT'),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'ACCOUNT_NUMBER', 'Business/customer-facing account number.',
     'Documented in the source warehouse as column ACCT_NBR.', 'ACCT_NBR', '30', 'VARCHAR2(30)', 2, 'N', 'N', 'Y', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'CLIENT_ID', 'Foreign key to the owning client (DIM_CLIENT).',
     'Documented in the source warehouse as column CLNT_SK.', 'CLNT_SK', '10', 'NUMBER(10)', 3, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'ACCOUNT_TYPE', 'Account wrapper type, e.g. Brokerage, IRA, 401k, Trust.',
     'Documented in the source warehouse as column ACCT_TYP_CD.', 'ACCT_TYP_CD', '30', 'VARCHAR2(30)', 4, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'ACCOUNT_STATUS', 'Current lifecycle status of the account.',
     'Documented in the source warehouse as column ACCT_STS_CD.', 'ACCT_STS_CD', '15', 'VARCHAR2(15)', 5, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'OPEN_DATE', 'Date the account was opened.',
     'Documented in the source warehouse as column OPEN_DT.', 'OPEN_DT', '', 'DATE', 6, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'CLOSE_DATE', 'Date the account was closed, if applicable.',
     'Documented in the source warehouse as column CLOSE_DT.', 'CLOSE_DT', '', 'DATE', 7, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'BASE_CURRENCY', 'ISO currency code the account is denominated in.',
     'Documented in the source warehouse as column BASE_CCY_CD.', 'BASE_CCY_CD', '3', 'VARCHAR2(3)', 8, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'CUSTODIAN_NAME', "Name of the custodian holding the account's assets.",
     'Documented in the source warehouse as column CUST_NM.', 'CUST_NM', '100', 'VARCHAR2(100)', 9, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_ACCOUNT', 'Dimension Table', 'ADVISOR_NAME', 'Name of the financial advisor servicing the account.',
     'Documented in the source warehouse as column ADVSR_NM.', 'ADVSR_NM', '100', 'VARCHAR2(100)', 10, 'N', 'Y', 'Y', ''),
    # ---- DIM_SECURITY -----------------------------------------
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'SECURITY_ID', 'Unique surrogate identifier for a security/instrument.',
     'Documented in the source warehouse as column SEC_SK.', 'SEC_SK', '10', 'NUMBER(10)', 1, 'Y', 'N', 'N', 'PK_DIM_SECURITY'),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'SECURITY_SYMBOL', 'Exchange ticker symbol for the security.',
     'Documented in the source warehouse as column SEC_SYM.', 'SEC_SYM', '15', 'VARCHAR2(15)', 2, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'CUSIP', 'CUSIP identifier for the security.',
     'Documented in the source warehouse as column CUSIP_ID.', 'CUSIP_ID', '9', 'VARCHAR2(9)', 3, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'SECURITY_NAME', 'Full descriptive name of the security.',
     'Documented in the source warehouse as column SEC_NM.', 'SEC_NM', '200', 'VARCHAR2(200)', 4, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'SECURITY_TYPE', 'Instrument type, e.g. Equity, Bond, ETF, Mutual Fund, Cash.',
     'Documented in the source warehouse as column SEC_TYP_CD.', 'SEC_TYP_CD', '30', 'VARCHAR2(30)', 5, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'ASSET_CLASS', 'Broad asset class grouping for the security.',
     'Documented in the source warehouse as column AST_CLS_CD.', 'AST_CLS_CD', '30', 'VARCHAR2(30)', 6, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'SECTOR', 'Industry sector classification of the issuer.',
     'Documented in the source warehouse as column SECT_NM.', 'SECT_NM', '50', 'VARCHAR2(50)', 7, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'CURRENCY', 'ISO currency code the security is priced in.',
     'Documented in the source warehouse as column CCY_CD.', 'CCY_CD', '3', 'VARCHAR2(3)', 8, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_SECURITY', 'Dimension Table', 'ISSUE_DATE', 'Date the security was issued.',
     'Documented in the source warehouse as column ISS_DT.', 'ISS_DT', '', 'DATE', 9, 'N', 'Y', 'N', ''),
    # ---- DIM_DATE ---------------------------------------------
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'DATE_ID', 'Surrogate key for the date, formatted YYYYMMDD.',
     'Documented in the source warehouse as column DT_SK.', 'DT_SK', '8', 'NUMBER(8)', 1, 'Y', 'N', 'N', 'PK_DIM_DATE'),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'CALENDAR_DATE', 'The calendar date value.',
     'Documented in the source warehouse as column CAL_DT.', 'CAL_DT', '', 'DATE', 2, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'CALENDAR_YEAR', 'Four-digit calendar year.',
     'Documented in the source warehouse as column CAL_YR.', 'CAL_YR', '4', 'NUMBER(4)', 3, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'CALENDAR_QUARTER', 'Calendar quarter number (1-4).',
     'Documented in the source warehouse as column CAL_QTR.', 'CAL_QTR', '1', 'NUMBER(1)', 4, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'CALENDAR_MONTH', 'Calendar month number (1-12).',
     'Documented in the source warehouse as column CAL_MNTH.', 'CAL_MNTH', '2', 'NUMBER(2)', 5, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'MONTH_NAME', 'Full name of the calendar month.',
     'Documented in the source warehouse as column MNTH_NM.', 'MNTH_NM', '15', 'VARCHAR2(15)', 6, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'DAY_OF_WEEK_NAME', 'Full name of the day of week.',
     'Documented in the source warehouse as column DOW_NM.', 'DOW_NM', '15', 'VARCHAR2(15)', 7, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'IS_MONTH_END', 'Flag indicating whether the date is the last day of the month.',
     'Documented in the source warehouse as column MNTH_END_FLG.', 'MNTH_END_FLG', '1', 'CHAR(1)', 8, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.DIM_DATE', 'Dimension Table', 'IS_TRADING_DAY', 'Flag indicating whether markets were open on the date.',
     'Documented in the source warehouse as column TRDNG_DAY_FLG.', 'TRDNG_DAY_FLG', '1', 'CHAR(1)', 9, 'N', 'N', 'N', ''),
    # ---- FACT_POSITION ----------------------------------------
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'ACCOUNT_ID', 'Foreign key to the account holding this position (DIM_ACCOUNT).',
     'Documented in the source warehouse as column ACCT_SK.', 'ACCT_SK', '10', 'NUMBER(10)', 1, 'Y', 'N', 'N', 'PK_FACT_POSITION'),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'SECURITY_ID', 'Foreign key to the security held in this position (DIM_SECURITY).',
     'Documented in the source warehouse as column SEC_SK.', 'SEC_SK', '10', 'NUMBER(10)', 2, 'Y', 'N', 'N', 'PK_FACT_POSITION'),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'AS_OF_DATE_ID', 'Foreign key to the snapshot date (DIM_DATE).',
     'Documented in the source warehouse as column AS_OF_DT_SK.', 'AS_OF_DT_SK', '8', 'NUMBER(8)', 3, 'Y', 'N', 'N', 'PK_FACT_POSITION'),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'QUANTITY', 'Number of units/shares of the security held.',
     'Documented in the source warehouse as column QTY.', 'QTY', '18,4', 'NUMBER(18,4)', 4, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'MARKET_PRICE', 'Price per unit as of the snapshot date, in local currency.',
     'Documented in the source warehouse as column MKT_PX.', 'MKT_PX', '18,6', 'NUMBER(18,6)', 5, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'MARKET_VALUE', 'Total market value of the position (QUANTITY x MARKET_PRICE).',
     'Documented in the source warehouse as column MKT_VAL_AMT.', 'MKT_VAL_AMT', '18,2', 'NUMBER(18,2)', 6, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'COST_BASIS', 'Total cost basis of the position.',
     'Documented in the source warehouse as column CST_BASIS_AMT.', 'CST_BASIS_AMT', '18,2', 'NUMBER(18,2)', 7, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'UNREALIZED_GAIN_LOSS', 'MARKET_VALUE minus COST_BASIS.',
     'Documented in the source warehouse as column UNRLZD_GL_AMT.', 'UNRLZD_GL_AMT', '18,2', 'NUMBER(18,2)', 8, 'N', 'Y', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'LOCAL_CURRENCY', 'ISO currency code of the monetary amounts on this row.',
     'Documented in the source warehouse as column LCL_CCY_CD.', 'LCL_CCY_CD', '3', 'VARCHAR2(3)', 9, 'N', 'N', 'N', ''),
    ('WEALTH_DB.PUBLIC.FACT_POSITION', 'Fact Table', 'POSITION_SOURCE_SYSTEM', 'System of record that supplied this position record.',
     'Documented in the source warehouse as column SRC_SYS_CD.', 'SRC_SYS_CD', '30', 'VARCHAR2(30)', 10, 'N', 'N', 'N', ''),
]

# File 2: metrics (aggregate measures), basic field-level synonyms, and
# custom_extensions placeholders for tables/fields -- all in one sheet,
# discriminated by the "Type" column. This feeds the *base* YAML
# generation together with the table/column metadata file.
ENRICHMENT_HEADERS = [
    "Type",
    "Table Name",
    "Column Name",
    "Metric Name",
    "Metric Expression",
    "Metric Description",
    "Metric Data Type",
    "Dialect",
    "Synonyms",
    "Custom Extension Vendor",
    "Custom Extension Data",
]

ENRICHMENT_ROWS = [
    # ---- Metrics: aggregate expressions spanning FACT_POSITION ----------
    # "Dialect" is per-metric (there's no global dialect setting anywhere
    # else in the app); blank/omitted defaults to ANSI_SQL.
    ("Metric", "", "", "total_market_value", "SUM(FACT_POSITION.MARKET_VALUE)",
     "Total market value of all positions across accounts.", "Decimal", "ANSI_SQL", "", "", ""),
    ("Metric", "", "", "total_cost_basis", "SUM(FACT_POSITION.COST_BASIS)",
     "Total cost basis of all positions.", "Decimal", "ANSI_SQL", "", "", ""),
    ("Metric", "", "", "total_unrealized_gain_loss", "SUM(FACT_POSITION.UNREALIZED_GAIN_LOSS)",
     "Total unrealized gain or loss across all positions.", "Decimal", "ANSI_SQL", "", "", ""),
    ("Metric", "", "", "distinct_securities_held", "COUNT(DISTINCT FACT_POSITION.SECURITY_ID)",
     "Number of distinct securities held across all positions.", "Integer", "ANSI_SQL", "", "", ""),
    ("Metric", "", "", "average_position_value", "AVG(FACT_POSITION.MARKET_VALUE)",
     "Average market value per position.", "Decimal", "ANSI_SQL", "", "", ""),

    # ---- Field synonyms ---------------------------------------------------
    ("Synonym", "FACT_POSITION", "MARKET_VALUE", "", "", "", "", "",
     "position value, holding value, market val", "", ""),
    ("Synonym", "FACT_POSITION", "QUANTITY", "", "", "", "", "",
     "units held, shares held, position size", "", ""),
    ("Synonym", "FACT_POSITION", "UNREALIZED_GAIN_LOSS", "", "", "", "", "",
     "unrealized P&L, paper gain, paper loss", "", ""),
    ("Synonym", "DIM_ACCOUNT", "ACCOUNT_TYPE", "", "", "", "", "",
     "account category, wrapper type, plan type", "", ""),
    ("Synonym", "DIM_ACCOUNT", "ACCOUNT_NUMBER", "", "", "", "", "",
     "account number, acct no", "", ""),
    ("Synonym", "DIM_CLIENT", "CLIENT_NAME", "", "", "", "", "",
     "client name, customer name, household name", "", ""),
    ("Synonym", "DIM_CLIENT", "RISK_PROFILE", "", "", "", "", "",
     "risk tolerance, investor risk level", "", ""),
    ("Synonym", "DIM_SECURITY", "SECURITY_TYPE", "", "", "", "", "",
     "asset type, instrument type, product type", "", ""),

    # ---- Custom extension placeholders (table-level and field-level) ----
    ("Custom Extension", "FACT_POSITION", "", "", "", "", "", "", "",
     "SNOWFLAKE", '{"clustering_keys": ["AS_OF_DATE_ID", "ACCOUNT_ID"]}'),
    ("Custom Extension", "DIM_ACCOUNT", "ACCOUNT_NUMBER", "", "", "", "", "", "",
     "COMMON", '{"masking_policy": "MASK_ACCOUNT_NUMBER"}'),
]

RELATIONSHIP_HEADERS = [
    "Relationship Name",
    "From Table",
    "From Columns",
    "To Table",
    "To Columns",
    "Relationship Type",
    "Description",
]

RELATIONSHIP_ROWS = [
    ("FACT_POSITION_TO_ACCOUNT", "FACT_POSITION", "ACCOUNT_ID", "DIM_ACCOUNT", "ACCOUNT_ID",
     "Many-to-One", "Each position belongs to exactly one account."),
    ("FACT_POSITION_TO_SECURITY", "FACT_POSITION", "SECURITY_ID", "DIM_SECURITY", "SECURITY_ID",
     "Many-to-One", "Each position holds exactly one security."),
    ("FACT_POSITION_TO_DATE", "FACT_POSITION", "AS_OF_DATE_ID", "DIM_DATE", "DATE_ID",
     "Many-to-One", "Each position snapshot is tied to exactly one calendar date."),
    ("ACCOUNT_TO_CLIENT", "DIM_ACCOUNT", "CLIENT_ID", "DIM_CLIENT", "CLIENT_ID",
     "Many-to-One", "Each account is owned by exactly one client/household."),
]

# File 4: AI context ENRICHMENT -- uploaded *after* a base YAML already
# exists. Instructions/Synonyms/Examples are concatenated onto whatever a
# field/table/model already has (e.g. synonyms from File 2); they never
# overwrite. "Custom Extension" is appended as a new custom_extensions
# entry (vendor_name: AI_ENRICHMENT).
AI_CONTEXT_HEADERS = [
    "Table Name",
    "Column Name",
    "Instructions",
    "Synonyms",
    "Examples",
    "Custom Extension",
]

AI_CONTEXT_ROWS = [
    ("MODEL", "",
     "Use this semantic model to answer questions about investment clients, their accounts, "
     "the securities they hold, and daily position valuations. FACT_POSITION is the daily grain "
     "fact table; DIM_ACCOUNT, DIM_CLIENT, DIM_SECURITY, and DIM_DATE provide descriptive context.",
     "portfolio model, wealth management model, investment book of record",
     "What is the total market value by account as of the latest date?; "
     "Show the top 10 holdings by market value for a given client; "
     "How has unrealized gain/loss trended over the last quarter?",
     '{"generated_by": "AI enrichment pass", "review_status": "pending"}'),
    ("FACT_POSITION", "",
     "Daily snapshot of security holdings per account. One row per account/security/date "
     "combination captures the quantity held and its valuation as of that date.",
     "holdings, positions, portfolio snapshot, book of record",
     "Total market value by account; positions with the largest unrealized loss today",
     '{"sensitivity": "confidential", "data_owner": "Portfolio Analytics Team"}'),
    # NOTE: MARKET_VALUE already has base synonyms from File 2 (Synonym row);
    # these are ADDITIONAL synonyms that get concatenated onto that list,
    # not a replacement for it.
    ("FACT_POSITION", "MARKET_VALUE",
     "Primary measure of position value; equals QUANTITY multiplied by MARKET_PRICE, in LOCAL_CURRENCY.",
     "mkt value, MV",
     "",
     '{"lineage": "derived = QUANTITY * MARKET_PRICE"}'),
    ("FACT_POSITION", "UNREALIZED_GAIN_LOSS",
     "Computed as MARKET_VALUE minus COST_BASIS. Positive values indicate an unrealized gain, "
     "negative values indicate an unrealized loss.",
     "unrealized PnL",
     "",
     ""),
    ("FACT_POSITION", "QUANTITY",
     "Number of shares/units of the security held in the account as of the snapshot date.",
     "",
     "",
     ""),
    ("DIM_ACCOUNT", "",
     "One row per investment account owned by a client. Slowly changing dimension tracked via "
     "OPEN_DATE/CLOSE_DATE and ACCOUNT_STATUS.",
     "accounts, portfolios, investment accounts",
     "",
     ""),
    ("DIM_ACCOUNT", "ACCOUNT_TYPE",
     "The account wrapper/tax treatment, e.g. Brokerage, IRA, 401k, or Trust.",
     "",
     "",
     ""),
    ("DIM_ACCOUNT", "ACCOUNT_NUMBER",
     "Customer-facing account number. Treat as sensitive; avoid exposing in shared reports.",
     "",
     "",
     ""),
    ("DIM_CLIENT", "",
     "One row per client or household relationship that owns one or more accounts.",
     "customers, households, investors",
     "",
     ""),
    ("DIM_CLIENT", "CLIENT_NAME",
     "Legal name of the client. Contains PII; mask or omit in externally shared results.",
     "",
     "",
     ""),
    ("DIM_CLIENT", "RISK_PROFILE",
     "Client's stated risk tolerance, used to evaluate portfolio suitability.",
     "",
     "",
     ""),
    ("DIM_SECURITY", "",
     "One row per tradable security or instrument that can appear in a position.",
     "instruments, holdings reference, products, securities master",
     "",
     ""),
    ("DIM_SECURITY", "SECURITY_TYPE",
     "Broad instrument category, e.g. Equity, Bond, ETF, Mutual Fund, or Cash.",
     "",
     "",
     ""),
    ("DIM_DATE", "",
     "Standard calendar date dimension used to slice and trend positions by day, month, "
     "quarter, or year.",
     "calendar, dates, calendar dimension",
     "",
     ""),
]


def _write_csv(path, headers, rows):
    for i, row in enumerate(rows):
        if len(row) != len(headers):
            raise ValueError(
                f"{path}: row {i} has {len(row)} values but header has {len(headers)} columns: {row}"
            )
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    _write_csv(os.path.join(OUT_DIR, "01_table_column_metadata.csv"), METADATA_HEADERS, METADATA_ROWS)
    _write_csv(os.path.join(OUT_DIR, "02_metrics_synonyms_extensions.csv"), ENRICHMENT_HEADERS, ENRICHMENT_ROWS)
    _write_csv(os.path.join(OUT_DIR, "03_relationships.csv"), RELATIONSHIP_HEADERS, RELATIONSHIP_ROWS)
    _write_csv(os.path.join(OUT_DIR, "04_ai_context.csv"), AI_CONTEXT_HEADERS, AI_CONTEXT_ROWS)
    print(f"Wrote sample files to {OUT_DIR}")


if __name__ == "__main__":
    main()
