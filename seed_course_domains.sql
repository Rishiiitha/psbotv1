-- Idempotently seed inferred course-domain mappings from the cleaned course catalog.
-- Review inferred rows before using them as authoritative academic classifications.
WITH catalog AS (
    SELECT DISTINCT BTRIM(vertical_name) AS vertical_name, BTRIM(course_name) AS course_name, course_level
    FROM ps_cleared_course_view_cleaned
), rules(domain_name, pattern, mapping_basis) AS (
    VALUES
        ('Data Science & Analytics', 'data science|data analytics|business analytics|bioinformatics|data mining|data warehouse', 'Course or vertical title indicates data science, analytics, or bioinformatics'),
        ('AI & Machine Learning', 'artificial intelligence|machine learning|deep learning|(^|[^a-z])ai([^a-z]|$)|computer vision|natural language processing|reinforcement learning|generative ai|neural network', 'Course or vertical title indicates AI or machine learning'),
        ('Programming & Languages', 'programming|c\+\+|(^|[^a-z])java([^a-z]|$)|python|javascript|coding|code debugging|object oriented|oops', 'Course or vertical title indicates programming or a programming language'),
        ('Web & Frontend', 'web|html|css|javascript|(^|[^a-z])react([^a-z]|$)|front.?end|user interface|ui.?ux|full.?stack', 'Course or vertical title indicates web or frontend development'),
        ('Backend & Databases', 'database|dbms|(^|[^a-z])sql([^a-z]|$)|back.?end|server.?side|api|data management', 'Course or vertical title indicates databases or backend/server-side development'),
        ('Cloud & DevOps', 'cloud|devops|linux|version control|(^|[^a-z])git([^a-z]|$)|container|docker|kubernetes', 'Course or vertical title indicates cloud, DevOps, Linux, or version control'),
        ('Networking & Cybersecurity', 'computer networking|network.*cse.*core|cse.*core.*network|cyber.?security|information security|cryptograph|(^|[^a-z])security([^a-z]|$)', 'Course or vertical title indicates IT networking or cybersecurity'),
        ('Software Engineering & Testing', 'software engineering|software testing|testing automation|quality assurance|test automation', 'Course or vertical title indicates software engineering or testing'),
        ('Operating Systems & Computer Systems', 'operating system|computer organization|computer architecture|embedded system|microprocessor|real.?time operating system|system design', 'Course or vertical title indicates operating systems or computer systems'),
        ('Mobile Development', 'mobile app|android app|app development', 'Course or vertical title indicates mobile application development'),
        ('Full Stack & DevOps', 'full.?stack|devops', 'Course or vertical title explicitly indicates full-stack or DevOps')
), inferred AS (
    SELECT DISTINCT c.vertical_name, c.course_name, c.course_level, r.domain_name, r.mapping_basis
    FROM catalog c CROSS JOIN rules r
    WHERE CONCAT_WS(' ', c.vertical_name, c.course_name) ~* r.pattern
)
INSERT INTO ps_course_domain_map
    (vertical_name, course_name, course_level, domain_name, mapping_method, confidence, reviewed, mapping_basis)
SELECT vertical_name, course_name, course_level, domain_name, 'inferred', 0.850, FALSE, mapping_basis
FROM inferred
ON CONFLICT (vertical_name, course_name, course_level, domain_name) DO NOTHING;

INSERT INTO ps_course_domain_map
    (vertical_name, course_name, course_level, domain_name, mapping_method, confidence, reviewed, mapping_basis)
SELECT DISTINCT BTRIM(vertical_name), BTRIM(course_name), course_level, 'Core Courses', 'existing_vertical', 0.980, FALSE,
       'Explicit Core marker in the existing vertical or course title'
FROM ps_cleared_course_view_cleaned
WHERE vertical_name ILIKE '%core concept%'
   OR vertical_name ILIKE '%core concepts%'
   OR vertical_name ILIKE '%core conepts%'
   OR BTRIM(vertical_name) ILIKE 'S7 Core'
   OR course_name ILIKE '%CSE - core concept%'
ON CONFLICT (vertical_name, course_name, course_level, domain_name) DO NOTHING;

INSERT INTO ps_course_domain_map
    (vertical_name, course_name, course_level, domain_name, mapping_method, confidence, reviewed, mapping_basis)
SELECT DISTINCT m.vertical_name, m.course_name, m.course_level, 'IT', 'inferred', 0.900, FALSE,
       'Course has at least one IT subdomain mapping'
FROM ps_course_domain_map m
WHERE m.domain_name NOT IN ('IT', 'Core Courses')
ON CONFLICT (vertical_name, course_name, course_level, domain_name) DO NOTHING;

INSERT INTO ps_course_domain_map
    (vertical_name, course_name, course_level, domain_name, mapping_method, confidence, reviewed, mapping_basis)
SELECT DISTINCT m.vertical_name, m.course_name, m.course_level, 'Full Stack Development', 'inferred', 0.720, FALSE,
       'Mapped as a frontend, backend/database, or explicitly full-stack course component'
FROM ps_course_domain_map m
WHERE m.domain_name IN ('Web & Frontend', 'Backend & Databases', 'Full Stack & DevOps')
ON CONFLICT (vertical_name, course_name, course_level, domain_name) DO NOTHING;

-- Guard against broad substring matches such as "react" inside "reactions" and
-- electrical network-theory mock exams being mistaken for IT networking.
DELETE FROM ps_course_domain_map
WHERE domain_name = 'Web & Frontend'
  AND course_name ILIKE '%reaction%';
DELETE FROM ps_course_domain_map
WHERE domain_name = 'Networking & Cybersecurity'
  AND (course_name ILIKE 'NT Mock Test %' OR course_name ILIKE '%Two Port Networks%');
DELETE FROM ps_course_domain_map m
WHERE m.domain_name = 'IT'
  AND NOT EXISTS (
      SELECT 1 FROM ps_course_domain_map sub
      WHERE sub.vertical_name = m.vertical_name AND sub.course_name = m.course_name
        AND sub.course_level = m.course_level
        AND sub.domain_name NOT IN ('IT', 'Core Courses')
  );
