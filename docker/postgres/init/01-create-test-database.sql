-- Executed once by the postgres image when the data volume is empty.
-- Init scripts run as POSTGRES_USER, which becomes the owner.
CREATE DATABASE traceforge_test;
