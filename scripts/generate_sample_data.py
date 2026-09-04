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
    "size",
    "Technical Data Type",
    "Column Position",
    "Is Primary Key",
    "Is nullable",
    "Contains PII",
    "Primary Key",
]

# (table, asset_type, column, description, source_description, size, tech_type,
#  position, is_pk, is_nullable, contains_pii, pk_name)
METADATA_ROWS = [
    # ---- DIM_CLIENT ---------------------------------------------------
    ("DIM_CLIENT", "Dimension Table", "CLIENT_ID", "Unique surrogate identifier for a client or household.",
     "CLNT_SK", "10", "NUMBER(10)", 1, "Y", "N", "N", "PK_DIM_CLIENT"),
    ("DIM_CLIENT", "Dimension Table", "CLIENT_NAME", "Legal name of the client or household.",
     "CLNT_NM", "200", "VARCHAR2(200)", 2, "N", "N", "Y", ""),
    ("DIM_CLIENT", "Dimension Table", "CLIENT_TYPE", "Classification of the client, e.g. Individual or Institutional.",
     "CLNT_TYP_CD", "20", "VARCHAR2(20)", 3, "N", "N", "N", ""),
    ("DIM_CLIENT", "Dimension Table", "TAX_ID", "Government tax identifier (e.g. SSN/EIN) associated with the client.",
     "TAX_ID_NBR", "20", "VARCHAR2(20)", 4, "N", "Y", "Y", ""),
    ("DIM_CLIENT", "Dimension Table", "ONBOARDING_DATE", "Date the client relationship was established.",
     "ONBRD_DT", "", "DATE", 5, "N", "N", "N", ""),
    ("DIM_CLIENT", "Dimension Table", "RISK_PROFILE", "Client's stated investment risk tolerance.",
     "RISK_PRFL_CD", "20", "VARCHAR2(20)", 6, "N", "Y", "N", ""),
    ("DIM_CLIENT", "Dimension Table", "COUNTRY_CODE", "ISO country code of the client's residence.",
     "CNTRY_CD", "3", "VARCHAR2(3)", 7, "N", "N", "N", ""),

    # ---- DIM_ACCOUNT ----------------------------------------------------
    ("DIM_ACCOUNT", "Dimension Table", "ACCOUNT_ID", "Unique surrogate identifier for an investment account.",
     "ACCT_SK", "10", "NUMBER(10)", 1, "Y", "N", "N", "PK_DIM_ACCOUNT"),
    ("DIM_ACCOUNT", "Dimension Table", "ACCOUNT_NUMBER", "Business/customer-facing account number.",
     "ACCT_NBR", "30", "VARCHAR2(30)", 2, "N", "N", "Y", ""),
    ("DIM_ACCOUNT", "Dimension Table", "CLIENT_ID", "Foreign key to the owning client (DIM_CLIENT).",
     "CLNT_SK", "10", "NUMBER(10)", 3, "N", "N", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "ACCOUNT_TYPE", "Account wrapper type, e.g. Brokerage, IRA, 401k, Trust.",
     "ACCT_TYP_CD", "30", "VARCHAR2(30)", 4, "N", "N", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "ACCOUNT_STATUS", "Current lifecycle status of the account.",
     "ACCT_STS_CD", "15", "VARCHAR2(15)", 5, "N", "N", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "OPEN_DATE", "Date the account was opened.",
     "OPEN_DT", "", "DATE", 6, "N", "N", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "CLOSE_DATE", "Date the account was closed, if applicable.",
     "CLOSE_DT", "", "DATE", 7, "N", "Y", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "BASE_CURRENCY", "ISO currency code the account is denominated in.",
     "BASE_CCY_CD", "3", "VARCHAR2(3)", 8, "N", "N", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "CUSTODIAN_NAME", "Name of the custodian holding the account's assets.",
     "CUST_NM", "100", "VARCHAR2(100)", 9, "N", "Y", "N", ""),
    ("DIM_ACCOUNT", "Dimension Table", "ADVISOR_NAME", "Name of the financial advisor servicing the account.",
     "ADVSR_NM", "100", "VARCHAR2(100)", 10, "N", "Y", "Y", ""),

    # ---- DIM_SECURITY -----------------------------------------------------
    ("DIM_SECURITY", "Dimension Table", "SECURITY_ID", "Unique surrogate identifier for a security/instrument.",
     "SEC_SK", "10", "NUMBER(10)", 1, "Y", "N", "N", "PK_DIM_SECURITY"),
    ("DIM_SECURITY", "Dimension Table", "SECURITY_SYMBOL", "Exchange ticker symbol for the security.",
     "SEC_SYM", "15", "VARCHAR2(15)", 2, "N", "N", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "CUSIP", "CUSIP identifier for the security.",
     "CUSIP_ID", "9", "VARCHAR2(9)", 3, "N", "Y", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "SECURITY_NAME", "Full descriptive name of the security.",
     "SEC_NM", "200", "VARCHAR2(200)", 4, "N", "N", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "SECURITY_TYPE", "Instrument type, e.g. Equity, Bond, ETF, Mutual Fund, Cash.",
     "SEC_TYP_CD", "30", "VARCHAR2(30)", 5, "N", "N", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "ASSET_CLASS", "Broad asset class grouping for the security.",
     "AST_CLS_CD", "30", "VARCHAR2(30)", 6, "N", "N", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "SECTOR", "Industry sector classification of the issuer.",
     "SECT_NM", "50", "VARCHAR2(50)", 7, "N", "Y", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "CURRENCY", "ISO currency code the security is priced in.",
     "CCY_CD", "3", "VARCHAR2(3)", 8, "N", "N", "N", ""),
    ("DIM_SECURITY", "Dimension Table", "ISSUE_DATE", "Date the security was issued.",
     "ISS_DT", "", "DATE", 9, "N", "Y", "N", ""),

    # ---- DIM_DATE -----------------------------------------------------
    ("DIM_DATE", "Dimension Table", "DATE_ID", "Surrogate key for the date, formatted YYYYMMDD.",
     "DT_SK", "8", "NUMBER(8)", 1, "Y", "N", "N", "PK_DIM_DATE"),
    ("DIM_DATE", "Dimension Table", "CALENDAR_DATE", "The calendar date value.",
     "CAL_DT", "", "DATE", 2, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "CALENDAR_YEAR", "Four-digit calendar year.",
     "CAL_YR", "4", "NUMBER(4)", 3, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "CALENDAR_QUARTER", "Calendar quarter number (1-4).",
     "CAL_QTR", "1", "NUMBER(1)", 4, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "CALENDAR_MONTH", "Calendar month number (1-12).",
     "CAL_MNTH", "2", "NUMBER(2)", 5, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "MONTH_NAME", "Full name of the calendar month.",
     "MNTH_NM", "15", "VARCHAR2(15)", 6, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "DAY_OF_WEEK_NAME", "Full name of the day of week.",
     "DOW_NM", "15", "VARCHAR2(15)", 7, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "IS_MONTH_END", "Flag indicating whether the date is the last day of the month.",
     "MNTH_END_FLG", "1", "CHAR(1)", 8, "N", "N", "N", ""),
    ("DIM_DATE", "Dimension Table", "IS_TRADING_DAY", "Flag indicating whether markets were open on the date.",
     "TRDNG_DAY_FLG", "1", "CHAR(1)", 9, "N", "N", "N", ""),

    # ---- FACT_POSITION --------------------------------------------------
    ("FACT_POSITION", "Fact Table", "ACCOUNT_ID", "Foreign key to the account holding this position (DIM_ACCOUNT).",
     "ACCT_SK", "10", "NUMBER(10)", 1, "Y", "N", "N", "PK_FACT_POSITION"),
    ("FACT_POSITION", "Fact Table", "SECURITY_ID", "Foreign key to the security held in this position (DIM_SECURITY).",
     "SEC_SK", "10", "NUMBER(10)", 2, "Y", "N", "N", "PK_FACT_POSITION"),
    ("FACT_POSITION", "Fact Table", "AS_OF_DATE_ID", "Foreign key to the snapshot date (DIM_DATE).",
     "AS_OF_DT_SK", "8", "NUMBER(8)", 3, "Y", "N", "N", "PK_FACT_POSITION"),
    ("FACT_POSITION", "Fact Table", "QUANTITY", "Number of units/shares of the security held.",
     "QTY", "18,4", "NUMBER(18,4)", 4, "N", "N", "N", ""),
    ("FACT_POSITION", "Fact Table", "MARKET_PRICE", "Price per unit as of the snapshot date, in local currency.",
     "MKT_PX", "18,6", "NUMBER(18,6)", 5, "N", "N", "N", ""),
    ("FACT_POSITION", "Fact Table", "MARKET_VALUE", "Total market value of the position (QUANTITY x MARKET_PRICE).",
     "MKT_VAL_AMT", "18,2", "NUMBER(18,2)", 6, "N", "N", "N", ""),
    ("FACT_POSITION", "Fact Table", "COST_BASIS", "Total cost basis of the position.",
     "CST_BASIS_AMT", "18,2", "NUMBER(18,2)", 7, "N", "N", "N", ""),
    ("FACT_POSITION", "Fact Table", "UNREALIZED_GAIN_LOSS", "MARKET_VALUE minus COST_BASIS.",
     "UNRLZD_GL_AMT", "18,2", "NUMBER(18,2)", 8, "N", "Y", "N", ""),
    ("FACT_POSITION", "Fact Table", "LOCAL_CURRENCY", "ISO currency code of the monetary amounts on this row.",
     "LCL_CCY_CD", "3", "VARCHAR2(3)", 9, "N", "N", "N", ""),
    ("FACT_POSITION", "Fact Table", "POSITION_SOURCE_SYSTEM", "System of record that supplied this position record.",
     "SRC_SYS_CD", "30", "VARCHAR2(30)", 10, "N", "N", "N", ""),
]

SNAPSHOT_HEADERS = [
    "Table Name",
    "Snapshot Type",
    "Snapshot Frequency",
    "Snapshot Date Column",
    "Partition Column",
    "History Type",
    "Retention Period",
    "Source System",
    "Load Pattern",
]

SNAPSHOT_ROWS = [
    ("FACT_POSITION", "Incremental", "Daily", "AS_OF_DATE_ID", "AS_OF_DATE_ID",
     "Type 2 (append-only daily snapshot)", "7 Years", "PORTFOLIO_ACCOUNTING_SYSTEM", "Batch"),
    ("DIM_ACCOUNT", "Full", "Daily", "", "", "Type 2 (tracked via CLOSE_DATE)", "Indefinite",
     "CRM_SYSTEM", "Batch"),
    ("DIM_CLIENT", "Full", "Daily", "", "", "Type 1", "Indefinite", "CRM_SYSTEM", "Batch"),
    ("DIM_SECURITY", "Full", "Weekly", "", "", "Type 1", "Indefinite", "MARKET_DATA_VENDOR", "Batch"),
    ("DIM_DATE", "Full", "Static (generated once)", "", "", "Static", "Indefinite", "GENERATED", "Batch"),
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

AI_CONTEXT_HEADERS = [
    "Table Name",
    "Column Name",
    "Instructions",
    "Synonyms",
    "Examples",
]

AI_CONTEXT_ROWS = [
    ("MODEL", "",
     "Use this semantic model to answer questions about investment clients, their accounts, "
     "the securities they hold, and daily position valuations. FACT_POSITION is the daily grain "
     "fact table; DIM_ACCOUNT, DIM_CLIENT, DIM_SECURITY, and DIM_DATE provide descriptive context.",
     "portfolio model, wealth management model, investment book of record",
     "What is the total market value by account as of the latest date?; "
     "Show the top 10 holdings by market value for a given client; "
     "How has unrealized gain/loss trended over the last quarter?"),
    ("FACT_POSITION", "",
     "Daily snapshot of security holdings per account. One row per account/security/date "
     "combination captures the quantity held and its valuation as of that date.",
     "holdings, positions, portfolio snapshot, book of record",
     "Total market value by account; positions with the largest unrealized loss today"),
    ("FACT_POSITION", "MARKET_VALUE",
     "Primary measure of position value; equals QUANTITY multiplied by MARKET_PRICE, in LOCAL_CURRENCY.",
     "position value, holding value, market val",
     ""),
    ("FACT_POSITION", "UNREALIZED_GAIN_LOSS",
     "Computed as MARKET_VALUE minus COST_BASIS. Positive values indicate an unrealized gain, "
     "negative values indicate an unrealized loss.",
     "unrealized P&L, paper gain, paper loss",
     ""),
    ("FACT_POSITION", "QUANTITY",
     "Number of shares/units of the security held in the account as of the snapshot date.",
     "units held, shares held, position size",
     ""),
    ("DIM_ACCOUNT", "",
     "One row per investment account owned by a client. Slowly changing dimension tracked via "
     "OPEN_DATE/CLOSE_DATE and ACCOUNT_STATUS.",
     "accounts, portfolios, investment accounts",
     ""),
    ("DIM_ACCOUNT", "ACCOUNT_TYPE",
     "The account wrapper/tax treatment, e.g. Brokerage, IRA, 401k, or Trust.",
     "account category, wrapper type, plan type",
     ""),
    ("DIM_ACCOUNT", "ACCOUNT_NUMBER",
     "Customer-facing account number. Treat as sensitive; avoid exposing in shared reports.",
     "account number, acct no",
     ""),
    ("DIM_CLIENT", "",
     "One row per client or household relationship that owns one or more accounts.",
     "customers, households, investors",
     ""),
    ("DIM_CLIENT", "CLIENT_NAME",
     "Legal name of the client. Contains PII; mask or omit in externally shared results.",
     "client name, customer name, household name",
     ""),
    ("DIM_CLIENT", "RISK_PROFILE",
     "Client's stated risk tolerance, used to evaluate portfolio suitability.",
     "risk tolerance, investor risk level",
     ""),
    ("DIM_SECURITY", "",
     "One row per tradable security or instrument that can appear in a position.",
     "instruments, holdings reference, products, securities master",
     ""),
    ("DIM_SECURITY", "SECURITY_TYPE",
     "Broad instrument category, e.g. Equity, Bond, ETF, Mutual Fund, or Cash.",
     "asset type, instrument type, product type",
     ""),
    ("DIM_DATE", "",
     "Standard calendar date dimension used to slice and trend positions by day, month, "
     "quarter, or year.",
     "calendar, dates, calendar dimension",
     ""),
]


def _write_csv(path, headers, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    _write_csv(os.path.join(OUT_DIR, "01_table_column_metadata.csv"), METADATA_HEADERS, METADATA_ROWS)
    _write_csv(os.path.join(OUT_DIR, "02_snapshot_details.csv"), SNAPSHOT_HEADERS, SNAPSHOT_ROWS)
    _write_csv(os.path.join(OUT_DIR, "03_relationships.csv"), RELATIONSHIP_HEADERS, RELATIONSHIP_ROWS)
    _write_csv(os.path.join(OUT_DIR, "04_ai_context.csv"), AI_CONTEXT_HEADERS, AI_CONTEXT_ROWS)
    print(f"Wrote sample files to {OUT_DIR}")


if __name__ == "__main__":
    main()
