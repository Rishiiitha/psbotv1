CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ps_cleared_course_view (
    roll_number VARCHAR(15) NOT NULL,
    name VARCHAR(150) NOT NULL,
    vertical_name VARCHAR(150) NOT NULL,
    course_name VARCHAR(150) NOT NULL,
    date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    venue_name VARCHAR(150) NOT NULL,
    status VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ps_status ON ps_cleared_course_view (status);
CREATE INDEX IF NOT EXISTS idx_ps_vertical ON ps_cleared_course_view (vertical_name);
CREATE INDEX IF NOT EXISTS idx_ps_course ON ps_cleared_course_view (course_name);
CREATE INDEX IF NOT EXISTS idx_ps_roll ON ps_cleared_course_view (roll_number);

-- Filtered/normalized analysis copy. Populate with the course-cleaning workflow,
-- then run migrate_course_levels.sql to extract course levels from titles.
CREATE TABLE IF NOT EXISTS ps_cleared_course_view_cleaned (
    LIKE ps_cleared_course_view INCLUDING ALL
);
ALTER TABLE ps_cleared_course_view_cleaned
    ADD COLUMN IF NOT EXISTS course_level TEXT NOT NULL DEFAULT 'Level 1';

-- One row per student, vertical, course, and level; attempts remain a count.
CREATE TABLE IF NOT EXISTS ps_student_course_summary (
    roll_number VARCHAR(15) NOT NULL,
    name VARCHAR(150) NOT NULL,
    vertical_name VARCHAR(150) NOT NULL,
    course_name VARCHAR(150) NOT NULL,
    attempt_count BIGINT NOT NULL,
    cleared BOOLEAN NOT NULL,
    first_attempt_date DATE NOT NULL,
    last_attempt_date DATE NOT NULL,
    course_level TEXT NOT NULL DEFAULT 'Level 1',
    UNIQUE (roll_number, vertical_name, course_name, course_level)
);
CREATE INDEX IF NOT EXISTS ps_student_course_summary_level_idx
    ON ps_student_course_summary (course_level);

-- Optional semantic-search index. Run embed_index.py after importing the data.
CREATE TABLE IF NOT EXISTS course_embeddings (
    id BIGSERIAL PRIMARY KEY,
    vertical_name TEXT NOT NULL,
    course_name TEXT NOT NULL,
    course_level TEXT NOT NULL DEFAULT 'Level 1',
    embedding VECTOR(384) NOT NULL,
    UNIQUE (vertical_name, course_name, course_level)
);

CREATE INDEX IF NOT EXISTS idx_course_embeddings_cosine
    ON course_embeddings USING hnsw (embedding vector_cosine_ops);

-- Curated many-to-many course-to-domain taxonomy. A course can map to several domains.
CREATE TABLE IF NOT EXISTS ps_course_domain_map (
    id BIGSERIAL PRIMARY KEY,
    vertical_name TEXT NOT NULL,
    course_name TEXT NOT NULL,
    course_level TEXT NOT NULL DEFAULT 'Level 1',
    domain_name TEXT NOT NULL,
    mapping_method TEXT NOT NULL DEFAULT 'inferred'
        CHECK (mapping_method IN ('inferred', 'manual', 'existing_vertical')),
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0.750
        CHECK (confidence >= 0 AND confidence <= 1),
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    mapping_basis TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (vertical_name, course_name, course_level, domain_name)
);

CREATE INDEX IF NOT EXISTS ps_course_domain_map_domain_idx
    ON ps_course_domain_map (LOWER(domain_name));
CREATE INDEX IF NOT EXISTS ps_course_domain_map_course_idx
    ON ps_course_domain_map (LOWER(course_name));
CREATE INDEX IF NOT EXISTS ps_course_domain_map_level_idx
    ON ps_course_domain_map (course_level);
