-- Normalize level suffixes in the derived course tables and pgvector index.
-- Keeps the original ps_cleared_course_view unchanged.
BEGIN;

CREATE TEMP TABLE _course_level_normalization ON COMMIT DROP AS
WITH source_names AS (
    SELECT DISTINCT BTRIM(vertical_name) AS vertical_name, BTRIM(course_name) AS original_course_name
    FROM ps_cleared_course_view_cleaned
    UNION
    SELECT DISTINCT BTRIM(vertical_name), BTRIM(course_name)
    FROM course_embeddings
), parsed AS (
    SELECT vertical_name, original_course_name,
           COALESCE(
               (regexp_match(original_course_name, '(?i)level\s*[-:]?\s*([0-9]+(?:\.[0-9]+)?[[:alpha:]]?)'))[1],
               (regexp_match(original_course_name, '-\s*([0-9]+(?:\.[0-9]+)?[[:alpha:]]?)\s*$'))[1],
               '1'
           ) AS level_value,
           BTRIM(REGEXP_REPLACE(
               REGEXP_REPLACE(original_course_name,
                   '(?i)\s*-?\s*level\s*[-:]?\s*[0-9]+(?:\.[0-9]+)?[[:alpha:]]?', '', 'g'),
               '\s*-\s*[0-9]+(?:\.[0-9]+)?[[:alpha:]]?\s*$', '', 'g'
           )) AS base_course_name
    FROM source_names
)
SELECT vertical_name, original_course_name, base_course_name,
       'Level ' || UPPER(level_value) AS course_level
FROM parsed;

ALTER TABLE ps_cleared_course_view_cleaned
    ADD COLUMN course_level TEXT NOT NULL DEFAULT 'Level 1';
UPDATE ps_cleared_course_view_cleaned AS target
SET course_name = parsed.base_course_name,
    course_level = parsed.course_level
FROM _course_level_normalization AS parsed
WHERE BTRIM(target.vertical_name) = parsed.vertical_name
  AND BTRIM(target.course_name) = parsed.original_course_name;

DROP INDEX IF EXISTS ps_student_course_summary_uq;
ALTER TABLE ps_student_course_summary
    ADD COLUMN course_level TEXT NOT NULL DEFAULT 'Level 1';
TRUNCATE TABLE ps_student_course_summary;
INSERT INTO ps_student_course_summary
    (roll_number, name, vertical_name, course_name, attempt_count, cleared,
     first_attempt_date, last_attempt_date, course_level)
SELECT roll_number,
       MIN(BTRIM(name)),
       MIN(BTRIM(vertical_name)),
       MIN(BTRIM(course_name)),
       COUNT(*)::BIGINT,
       BOOL_OR(LOWER(BTRIM(status)) = 'cleared'),
       MIN(date),
       MAX(date),
       MIN(course_level)
FROM ps_cleared_course_view_cleaned
GROUP BY roll_number, LOWER(BTRIM(vertical_name)), LOWER(BTRIM(course_name)), course_level;
CREATE UNIQUE INDEX ps_student_course_summary_uq
    ON ps_student_course_summary (roll_number, vertical_name, course_name, course_level);
CREATE INDEX IF NOT EXISTS ps_student_course_summary_level_idx
    ON ps_student_course_summary (course_level);

ALTER TABLE ps_course_domain_map
    DROP CONSTRAINT IF EXISTS ps_course_domain_map_vertical_name_course_name_domain_name_key;
ALTER TABLE ps_course_domain_map
    ADD COLUMN course_level TEXT NOT NULL DEFAULT 'Level 1';
UPDATE ps_course_domain_map AS target
SET course_name = parsed.base_course_name,
    course_level = parsed.course_level
FROM _course_level_normalization AS parsed
WHERE BTRIM(target.vertical_name) = parsed.vertical_name
  AND BTRIM(target.course_name) = parsed.original_course_name;

CREATE TEMP TABLE _course_domain_map_dedup ON COMMIT DROP AS
SELECT MIN(id) AS id,
       MIN(vertical_name) AS vertical_name,
       MIN(course_name) AS course_name,
       MIN(domain_name) AS domain_name,
       course_level,
       CASE WHEN BOOL_OR(mapping_method = 'manual') THEN 'manual'
            WHEN BOOL_OR(mapping_method = 'existing_vertical') THEN 'existing_vertical'
            ELSE 'inferred' END AS mapping_method,
       MAX(confidence) AS confidence,
       BOOL_OR(reviewed) AS reviewed,
       STRING_AGG(DISTINCT mapping_basis, '; ' ORDER BY mapping_basis) AS mapping_basis,
       MIN(created_at) AS created_at
FROM ps_course_domain_map
GROUP BY LOWER(BTRIM(vertical_name)), LOWER(BTRIM(course_name)), LOWER(BTRIM(domain_name)), course_level;
DELETE FROM ps_course_domain_map;
INSERT INTO ps_course_domain_map
    (id, vertical_name, course_name, domain_name, course_level, mapping_method,
     confidence, reviewed, mapping_basis, created_at)
SELECT id, vertical_name, course_name, domain_name, course_level, mapping_method,
       confidence, reviewed, mapping_basis, created_at
FROM _course_domain_map_dedup;
ALTER TABLE ps_course_domain_map
    ADD CONSTRAINT ps_course_domain_map_course_level_domain_key
    UNIQUE (vertical_name, course_name, course_level, domain_name);
CREATE INDEX IF NOT EXISTS ps_course_domain_map_level_idx
    ON ps_course_domain_map (course_level);

ALTER TABLE course_embeddings
    DROP CONSTRAINT IF EXISTS course_embeddings_vertical_name_course_name_key;
ALTER TABLE course_embeddings
    ADD COLUMN course_level TEXT NOT NULL DEFAULT 'Level 1';
UPDATE course_embeddings AS target
SET course_name = parsed.base_course_name,
    course_level = parsed.course_level
FROM _course_level_normalization AS parsed
WHERE BTRIM(target.vertical_name) = parsed.vertical_name
  AND BTRIM(target.course_name) = parsed.original_course_name;

CREATE TEMP TABLE _course_embeddings_dedup ON COMMIT DROP AS
SELECT DISTINCT ON (LOWER(BTRIM(vertical_name)), LOWER(BTRIM(course_name)), course_level)
       id, vertical_name, course_name, course_level, embedding
FROM course_embeddings
ORDER BY LOWER(BTRIM(vertical_name)), LOWER(BTRIM(course_name)), course_level, id;
DELETE FROM course_embeddings;
INSERT INTO course_embeddings (id, vertical_name, course_name, course_level, embedding)
SELECT id, vertical_name, course_name, course_level, embedding
FROM _course_embeddings_dedup;
ALTER TABLE course_embeddings
    ADD CONSTRAINT course_embeddings_vertical_course_level_key
    UNIQUE (vertical_name, course_name, course_level);
CREATE INDEX IF NOT EXISTS course_embeddings_level_idx
    ON course_embeddings (course_level);

CREATE INDEX IF NOT EXISTS idx_ps_cleaned_course_level
    ON ps_cleared_course_view_cleaned (course_level);

COMMIT;
