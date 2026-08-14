-- Provisions the read-only role the report service connects with.
--
-- This sits in infrastructure rather than in code on purpose: a credential
-- outlives every future code path a test did not anticipate. The report service
-- is a read-side fold and must not be able to write to the log even if some later
-- refactor hands it a session that could.
--
-- The grants use ALTER DEFAULT PRIVILEGES because the tables do not exist yet —
-- U6 creates them, as the owner, and this makes SELECT apply to them on creation
-- rather than requiring a second provisioning step after the DDL lands.

CREATE ROLE report_reader LOGIN PASSWORD 'report_reader';

GRANT CONNECT ON DATABASE companyos TO report_reader;
GRANT USAGE ON SCHEMA public TO report_reader;

-- Existing tables (none yet, but this keeps the script idempotent in intent if
-- it is ever re-run against a provisioned database).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO report_reader;

-- Anything the owner creates from here on: SELECT only, and explicitly no
-- INSERT, UPDATE, DELETE or TRUNCATE.
ALTER DEFAULT PRIVILEGES FOR ROLE companyos IN SCHEMA public
  GRANT SELECT ON TABLES TO report_reader;

-- Sequences are readable so a reader can report a cursor position, but the log's
-- sequence is kernel-assigned rather than store-generated (R37), so this is
-- belt-and-braces rather than load-bearing.
ALTER DEFAULT PRIVILEGES FOR ROLE companyos IN SCHEMA public
  GRANT SELECT ON SEQUENCES TO report_reader;

-- Schema creation belongs to the kernel's lease-gated path or a one-shot step,
-- never to a reader.
REVOKE CREATE ON SCHEMA public FROM report_reader;
