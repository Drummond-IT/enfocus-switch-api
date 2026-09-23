-- Pace read-only queries for the Switch connector.
--
-- HOW TO USE
--   1. Copy to ~/.config/enfocus-switch-mcp/pace_queries.sql
--   2. Replace every TODO with SQL for YOUR Pace database. Table and column names below are
--      placeholders: check them against your Pace schema with your Pace administrator.
--   3. Keep the column ALIASES exactly as written (the connector reads them by name).
--   4. Set PACE_DB_DSN (a READ-ONLY database role, ideally on a replica) and
--      PACE_QUERIES_FILE in config.env, then run `enfocus-switch-mcp check`.
--
-- RULES
--   * One SELECT per query. Parameters use %(name)s. The connector runs every query in a
--     read-only transaction with a statement timeout, and refuses anything but SELECT/WITH.
--   * Write a literal % as %% (e.g. LIKE 'ABC%%'), and don't put "--" inside string literals.
--   * Sizes in inches. Inks as numbers (4 = CMYK, 1 = black). spot_colors as a comma list.

-- name: job_spec
-- Params: %(job_number)s
-- Must return one row with: job_number, customer, product, trim_width_in, trim_height_in, pages,
--   front_inks, back_inks, spot_colors, quantity, stock, binding
SELECT
    NULL::text    AS job_number,      -- TODO: job number column
    NULL::text    AS customer,        -- TODO: customer name (join the customer table)
    NULL::text    AS product,         -- TODO: product type / job part description
    NULL::numeric AS trim_width_in,   -- TODO: finished width (convert units if needed)
    NULL::numeric AS trim_height_in,  -- TODO: finished height
    NULL::int     AS pages,           -- TODO: pages in the customer file (2 for a 2-sided flat)
    NULL::int     AS front_inks,      -- TODO: colors side 1
    NULL::int     AS back_inks,       -- TODO: colors side 2
    NULL::text    AS spot_colors,     -- TODO: named/PMS inks, comma separated
    NULL::int     AS quantity,        -- TODO
    NULL::text    AS stock,           -- TODO: paper
    NULL::text    AS binding          -- TODO: e.g. 'saddle stitch'
WHERE %(job_number)s IS NOT NULL AND false  -- TODO: replace with FROM ... WHERE job = %(job_number)s

-- name: job_status
-- Params: %(job_number)s
-- Must return one row with: job_number, customer, status, due_date, csr, description
SELECT
    NULL::text AS job_number,
    NULL::text AS customer,
    NULL::text AS status,         -- TODO: current job status / milestone
    NULL::date AS due_date,       -- TODO: promise date
    NULL::text AS csr,            -- TODO: CSR name
    NULL::text AS description     -- TODO: job description
WHERE %(job_number)s IS NOT NULL AND false  -- TODO

-- name: similar_jobs
-- Params: %(trim_width_in)s, %(trim_height_in)s, %(pages)s, %(customer)s, %(limit)s (all may be NULL)
-- Must return rows with: job_number, customer, description, trim_width_in, trim_height_in, pages,
--   quantity, price, created
SELECT
    NULL::text AS job_number, NULL::text AS customer, NULL::text AS description,
    NULL::numeric AS trim_width_in, NULL::numeric AS trim_height_in, NULL::int AS pages,
    NULL::int AS quantity, NULL::numeric AS price, NULL::date AS created
WHERE false  -- TODO: recent jobs with matching size/pages, newest first, LIMIT %(limit)s
