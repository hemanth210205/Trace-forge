-- Executed once by the postgres image when the data volume is empty (never on an existing volume).
-- traceforge_dev is the default database of `python -m app.serve`; the server migrates it on first start.
CREATE DATABASE traceforge_dev;
