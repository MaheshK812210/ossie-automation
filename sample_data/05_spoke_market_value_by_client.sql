-- Sample SPOKE SQL: approved logic for total market value by client.
-- Attaching this file in the SPOKE section sends it through the LLM
-- gateway, which returns an instruction JSON enrichment on the Ossie model.

SELECT
    c.CLIENT_ID,
    c.CLIENT_NAME,
    SUM(p.MARKET_VALUE) AS total_market_value
FROM WEALTH_DB.PUBLIC.FACT_POSITION p
JOIN WEALTH_DB.PUBLIC.DIM_ACCOUNT a
    ON p.ACCOUNT_ID = a.ACCOUNT_ID
JOIN WEALTH_DB.PUBLIC.DIM_CLIENT c
    ON a.CLIENT_ID = c.CLIENT_ID
WHERE p.AS_OF_DATE_ID = (
    SELECT MAX(AS_OF_DATE_ID) FROM WEALTH_DB.PUBLIC.FACT_POSITION
)
GROUP BY c.CLIENT_ID, c.CLIENT_NAME
ORDER BY total_market_value DESC;
