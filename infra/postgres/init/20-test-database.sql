-- A separate database for the store suite.
--
-- The suite creates the schema, drops it between dialect runs, and takes the writer
-- lease. The application's database is where a running kernel does the same things — and
-- from U9 that kernel holds a lease and owns the schema for as long as it is up. Sharing
-- one database would make the two fight: the suite would drop tables from under a live
-- kernel, and the kernel's lease would make the suite's own acquisition fail for reasons
-- that have nothing to do with what it is testing.
--
-- Same server, same version, same init path — only the database name differs, so the
-- suite still exercises the real Postgres behaviour it exists to check.

CREATE DATABASE companyos_test OWNER companyos;
