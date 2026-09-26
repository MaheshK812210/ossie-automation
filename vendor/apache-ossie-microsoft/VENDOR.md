# Vendored: apache-ossie-microsoft

Source: [apache/ossie `converters/microsoft`](https://github.com/apache/ossie/tree/main/converters/microsoft)

This tree is a sparse checkout of the official Microsoft Power BI / Fabric
converter from the Apache Ossie (incubating) project. It is licensed under
the Apache License 2.0 — see `LICENSE` and `NOTICE` in this directory.

Install into the app environment with:

```bash
pip install -e ./vendor/apache-ossie-microsoft
```

(or `pip install -r requirements.txt` from the repo root).

Do not edit converter source here unless syncing from upstream. App-specific
wrapping (document shape unwrap/wrap, Snowflake partition post-process, PBIP
zip packaging) lives in `ossie_microsoft_bridge.py` at the repo root.
