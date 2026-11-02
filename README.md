# Student Skills WhatsApp Query Bot (PostgreSQL + pgvector + Groq)

This starter imports the supplied MySQL-style `ps_cleared_course_view` dump into PostgreSQL, answers natural-language questions with Groq-generated **read-only SQL**, and sends replies through the official WhatsApp Cloud API.

## Important data limitation
The imported source has these fields only: `roll_number`, `name`, `vertical_name`, `course_name`, `date`, `start_time`, `end_time`, `venue_name`, `status`. It has no explicit `skill`, `department`, GPA, or placement outcome columns. The derived `ps_cleared_course_view_cleaned` table stores normalized course names with a separate `course_level` (titles with no level use `Level 1`). The bot queries `ps_student_course_summary`, with one row per student, vertical, course, and level plus an `attempt_count` and a boolean `cleared` value.

## 1. Install PostgreSQL and pgAdmin
Install PostgreSQL for Windows (include Command Line Tools) and pgAdmin 4. Remember: pgAdmin's **master password** unlocks saved server passwords; it is not the PostgreSQL database user's password.

Open pgAdmin and connect to your local PostgreSQL server. Create database `skillsdb`.

## 2. Enable pgvector
Install pgvector for your exact PostgreSQL version/Windows build. If the installer/package you use does not include it, follow the official pgvector installation instructions for your platform. Then in pgAdmin Query Tool, connected to `skillsdb`, run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

If PostgreSQL says it cannot open `vector.control`, pgvector is not installed into that PostgreSQL server installation yet. Do not continue to embeddings until fixed.

## 3. Create table and import the dump
Open `schema.sql` in pgAdmin Query Tool and execute it. Then create a dedicated read-only login for the bot (change the password):

```sql
CREATE ROLE skillbot_readonly LOGIN PASSWORD 'REPLACE_WITH_A_LONG_RANDOM_PASSWORD';
GRANT CONNECT ON DATABASE skillsdb TO skillbot_readonly;
GRANT USAGE ON SCHEMA public TO skillbot_readonly;
GRANT SELECT ON ps_student_course_summary, course_embeddings, ps_course_domain_map TO skillbot_readonly;
```

Use a separate admin connection for importing. In PowerShell, from this project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your PostgreSQL connection string and Groq/WhatsApp credentials. For importing, temporarily set `DATABASE_URL` to your **admin** PostgreSQL connection in `.env` (or use a separate admin env file). Run:

```powershell
python import_mysql_dump.py "C:\path\to\ps_cleared_data.sql"
```

The script truncates the target table before importing, so use it only on a new/dedicated target table. The dump is around 46 MB and contains multiple INSERT blocks; allow it a few minutes. Verify in pgAdmin:

```sql
SELECT COUNT(*) FROM ps_cleared_course_view;
SELECT status, COUNT(*) FROM ps_cleared_course_view GROUP BY status;
SELECT * FROM ps_cleared_course_view LIMIT 5;
```

After importing, prepare the derived analysis tables from the imported attempts, then change `.env` back to the `skillbot_readonly` connection string. On the current database, `migrate_course_levels.sql` has already normalized course titles and added `course_level` to the cleaned attempts, student summary, domain mapping, and embedding tables. It is a one-time migration for that existing database; do not rerun it. For a fresh database, create/populate the cleaned attempts and student summary first, then use the same level extraction logic before loading domain mappings or embeddings.

## 4. Semantic course understanding with pgvector
Question answering now inspects the live PostgreSQL columns, retrieves related course/vertical names from pgvector, asks Groq to plan a read-only query from that context, and summarizes only the SQL result. Counts and rankings always come from SQL; embedding similarity is only used to find likely names. Overall questions do not need a course-name match.

When a title such as `DataScience` has both an exact course and related subcourses, the assistant asks whether to use that exact course or the wider domain. For a domain such as `backend` with no exact course title, it matches relevant titles from the database catalog and ranks students by the summed inferred course difficulty, then by the number of distinct related courses cleared. Difficulty is inferred from course/level names because the source table has no difficulty field; answers label it as inferred.

Course-to-domain assignments are stored in `ps_course_domain_map` and are read by the question planner. The mapping supports overlapping domains, includes a confidence and rationale, and leaves `reviewed=false` until an owner approves each assignment. The current seed includes IT subdomains, a broad `IT` label, a composite `Full Stack Development` mapping, and catalog-marked core courses. After preparing `ps_cleared_course_view_cleaned` and extracting course levels, mappings can be created or refreshed with `psql "postgresql://USER:PASSWORD@HOST:PORT/DATABASE" -f seed_course_domains.sql`. Grant the bot role `SELECT` access to this table as shown above.

`sentence-transformers` is included in `requirements.txt`. On the first local run, it downloads `all-MiniLM-L6-v2` (internet access and additional disk space are needed for the Python/model dependencies). Set `ENABLE_SEMANTIC_SEARCH=false` to skip vector retrieval temporarily; direct schema-aware SQL planning still works. To build or refresh the course index after importing data, temporarily set `DATABASE_URL` to a database role that can insert/update `course_embeddings`, then run:

```powershell
python embed_index.py
```

Restore the read-only bot `DATABASE_URL` after indexing. The deployed bot only needs `SELECT` access to both tables.

This creates embeddings for distinct vertical/course names and the HNSW cosine index is declared in `schema.sql`. To query nearest matches in pgAdmin:

```sql
SELECT vertical_name, course_name, 1 - (embedding <=> (
  SELECT embedding FROM course_embeddings LIMIT 1
)) AS similarity
FROM course_embeddings
ORDER BY embedding <=> (
  SELECT embedding FROM course_embeddings LIMIT 1
)
LIMIT 10;
```

The web and WhatsApp question path uses parameterized query embeddings to retrieve the eight nearest course/vertical pairs as planner context. If the extension/table/index or embedding package is unavailable, the service logs that semantic retrieval is unavailable and continues with schema-aware SQL planning.

## 5. Get Groq API key
Create an API key in the Groq console and put it in `.env` as `GROQ_API_KEY`. Do not commit `.env` or expose the key in browser/client code. You can change `GROQ_MODEL` to a model currently available to your Groq account.

## 6. Configure WhatsApp Cloud API
In Meta for Developers:
1. Create an app and add the WhatsApp product.
2. Use the test sender/recipient setup first; add and verify your own recipient number as directed in the dashboard.
3. Copy the temporary/permanent access token and Phone Number ID into `.env`.
4. Deploy this FastAPI service on a public HTTPS host (or use a temporary HTTPS tunnel for local testing).
5. In WhatsApp Configuration, set the webhook callback URL to `https://YOUR_PUBLIC_HOST/webhook`, and the verify token to exactly `WHATSAPP_VERIFY_TOKEN` from `.env`.
6. Subscribe the app to the `messages` webhook field.
7. Send a message to the test WhatsApp number and check the app logs.

WhatsApp Cloud API is not the same as automating WhatsApp Web. Meta may require business/app setup and message-template rules for business-initiated conversations. For replies inside the customer service window, free-form replies are generally allowed subject to current Meta policy.

## 7. Run locally
```powershell
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```
Open `http://127.0.0.1:8000/` to use the web dashboard. It sends questions to
the same read-only query pipeline as WhatsApp. The health endpoint is
`http://127.0.0.1:8000/health`. For webhook testing, expose port 8000 through
an HTTPS tunnel and configure that URL in Meta.

## 8. Example questions
- `How many distinct students have cleared Programming Java?`
- `Top 10 students with the most cleared Java-related courses`
- `Count students with cleared courses whose names mention Python`
- `How many students have cleared Database Programming?`
- `Show cleared vs not-cleared counts for Programming Java`

Be careful with "full stack" and "backend": these terms do not appear as structured fields in the provided schema. For reliable rankings, maintain an approved mapping table from courses to skill categories (e.g., backend, frontend, full stack, Java) rather than trusting fuzzy model interpretation.

## 9. pgAdmin password forgotten?
There are two different passwords:
- **pgAdmin master password**: unlocks pgAdmin's locally saved credentials. It does not reset the PostgreSQL login. Depending on your pgAdmin version, use its password-reset/credential-reset option or reset the local pgAdmin configuration; this can remove saved server connections/passwords.
- **PostgreSQL server password** (often the `postgres` role): if another database superuser can log in, run `ALTER ROLE postgres WITH PASSWORD 'new-long-password';`. If no superuser can log in, use the official PostgreSQL Windows password-reset procedure for your installed version. It usually involves a temporary local authentication change in `pg_hba.conf`; back up the file, restrict any temporary trust rule to localhost, restart the correct PostgreSQL service, set the new password, restore the original authentication rule immediately, and restart again. Never leave `trust` enabled. Do not uninstall PostgreSQL or delete its data directory to reset a password.

## Security notes
- Keep `.env` private; add it to `.gitignore`.
- Use the read-only DB role in the running bot.
- Review generated SQL before production; a read-only DB role is the strongest safeguard.
- Add webhook signature verification, per-user access controls, rate limiting, audit logging, and privacy approval before using real student records.
