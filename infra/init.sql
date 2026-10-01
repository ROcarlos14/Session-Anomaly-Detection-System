-- Initialization SQL for PostgreSQL
-- This runs when the container first starts

CREATE DATABASE anomaly_detection_test;

\c anomaly_detection_development;

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
