-- Run once when the container first starts
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Optional: read-only reporting role
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'erp_reader') THEN
    CREATE ROLE erp_reader WITH LOGIN PASSWORD 'reader_pass';
    GRANT CONNECT ON DATABASE heavy_erp TO erp_reader;
  END IF;
END $$;
