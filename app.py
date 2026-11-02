import os, json, re
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from groq import Groq
from dotenv import load_dotenv
from db import fetch_rows
from sql_guard import validate_sql

load_dotenv()
app = FastAPI(title="Student Skills WhatsApp Query Bot")
FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
if MODEL == "llama-3.3-70b-versatile":
    # Groq decommissioned this model ID in August 2026. Remap stale deployment
    # settings so existing GROQ_MODEL environment variables do not break queries.
    MODEL = "openai/gpt-oss-20b"
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v22.0")

TABLE = "ps_student_course_summary"
STATIC_SCHEMA = """
Each row is one student, vertical, course, and course_level. attempt_count stores repeated attempts.
For student counts use COUNT(DISTINCT roll_number); for attempts use SUM(attempt_count).
The cleared column is boolean. Use cleared IS TRUE for cleared/passed and cleared IS FALSE otherwise.
course_level is a separate canonical value such as Level 1, Level 1A, Level 2.0; titles without a level default to Level 1.
For level counts count distinct (course_name, course_level), not attempts.
An overall question without a course/vertical filter applies across all rows.
The source has no department, skill, GPA, or placement columns. Never invent them.
Use only the approved table and discovered columns. Return one read-only SELECT.
Use LIMIT <= 100 for row lists.
"""

_schema_cache = None
_embedding_model = None
_vector_index_ready = None
USER_SESSIONS = {}

def database_schema() -> list[dict]:
    """Inspect live PostgreSQL columns so SQL generation follows the deployed schema."""
    global _schema_cache
    if _schema_cache is None:
        _, rows = fetch_rows(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
            (TABLE,),
        )
        if not rows:
            raise RuntimeError(f"Database table {TABLE} was not found or has no visible columns.")
        _schema_cache = [{"name": row[0], "type": row[1]} for row in rows]
    return _schema_cache

def semantic_candidates(question: str) -> list[dict]:
    """Retrieve likely course/vertical matches with pgvector; SQL remains the source of counts."""
    global _embedding_model, _vector_index_ready
    if os.getenv("ENABLE_SEMANTIC_SEARCH", "true").lower() not in {"1", "true", "yes"}:
        return []
    try:
        if _vector_index_ready is None:
            _, status = fetch_rows("SELECT EXISTS (SELECT 1 FROM course_embeddings LIMIT 1)")
            _vector_index_ready = bool(status and status[0][0])
        if not _vector_index_ready:
            return []
        if _embedding_model is None:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer(os.getenv(
                "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
            ))
        vector = _embedding_model.encode(question, normalize_embeddings=True)
        vector_text = "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"
        _, rows = fetch_rows(
            "SELECT vertical_name, course_name, course_level, "
            "1 - (embedding <=> %s::vector) AS similarity "
            "FROM course_embeddings ORDER BY embedding <=> %s::vector LIMIT 40",
            (vector_text, vector_text),
        )
        return [
            {"vertical": row[0], "course": row[1], "course_level": row[2], "similarity": round(float(row[3]), 3)}
            for row in rows
        ]
    except Exception as error:
        # The system can still plan exact SQL when vector extension/index/model is not installed.
        _vector_index_ready = False
        print(f"Semantic retrieval unavailable: {type(error).__name__}: {error}")
        return []

def relevant_catalog(question: str, semantic: list[dict]) -> list[dict]:
    """Combine literal database matches with semantic neighbors for exact/domain decisions."""
    stop = {"top", "show", "list", "find", "who", "students", "student", "in", "for", "the",
            "with", "most", "many", "count", "cleared", "passed", "course", "courses", "domain",
            "overall", "best", "ranked", "rank", "of", "across", "all", "their", "has", "have"}
    catalog_question = re.sub(r"\btop\s+\d+\b", " ", question, flags=re.I)
    terms = [term.lower() for term in re.findall(r"[a-zA-Z0-9]+", catalog_question) if term.lower() not in stop]
    found = {}
    if terms:
        conditions, params = [], []
        for term in terms:
            compact = re.sub(r"[^a-z0-9]", "", term)
            conditions.append(
                "(regexp_replace(LOWER(vertical_name), '[^a-z0-9]', '', 'g') LIKE %s "
                "OR regexp_replace(LOWER(course_name), '[^a-z0-9]', '', 'g') LIKE %s "
                "OR regexp_replace(LOWER(course_level), '[^a-z0-9]', '', 'g') LIKE %s)"
            )
            params.extend([f"%{compact}%", f"%{compact}%", f"%{compact}%"])
        try:
            _, rows = fetch_rows(
                "SELECT DISTINCT vertical_name, course_name, course_level FROM ps_student_course_summary WHERE "
                + " AND ".join(conditions) + " ORDER BY vertical_name, course_name, course_level LIMIT 100",
                tuple(params),
            )
            for vertical, course, level in rows:
                found[(vertical, course, level)] = {
                    "vertical": vertical, "course": course, "course_level": level, "source": "literal"
                }
        except Exception as error:
            print(f"Literal catalog search failed: {type(error).__name__}: {error}")
    if not semantic and not found:
        try:
            _, rows = fetch_rows(
                "SELECT DISTINCT vertical_name, course_name, course_level FROM ps_student_course_summary "
                "ORDER BY vertical_name, course_name, course_level"
            )
            for vertical, course, level in rows:
                found[(vertical, course, level)] = {
                    "vertical": vertical, "course": course, "course_level": level, "source": "full_catalog"
                }
        except Exception as error:
            print(f"Full catalog lookup failed: {type(error).__name__}: {error}")
    for item in semantic:
        key = (item.get("vertical"), item.get("course"), item.get("course_level", "Level 1"))
        found.setdefault(key, {**item, "source": "semantic"})
    return list(found.values())

def add_domain_mappings(catalog: list[dict]) -> list[dict]:
    """Attach the curated database taxonomy to the matching live course pairs."""
    try:
        _, rows = fetch_rows(
            "SELECT vertical_name, course_name, course_level, domain_name, confidence, reviewed "
            "FROM ps_course_domain_map ORDER BY domain_name, course_name, course_level"
        )
    except Exception as error:
        print(f"Course-domain mapping lookup unavailable: {type(error).__name__}: {error}")
        return catalog
    by_pair = {(item.get("vertical"), item.get("course"), item.get("course_level", "Level 1")): item for item in catalog}
    for vertical, course, level, domain, confidence, reviewed in rows:
        key = (vertical, course, level)
        item = by_pair.get(key)
        if item is None:
            item = {"vertical": vertical, "course": course, "course_level": level,
                    "source": "domain_map", "domains": []}
            catalog.append(item)
            by_pair[key] = item
        item.setdefault("domains", []).append({
            "name": domain, "confidence": float(confidence), "reviewed": bool(reviewed)
        })
    return catalog

def interpret_scope(question: str, catalog: list[dict]) -> dict:
    """Decide whether the user means one course or a domain made of related courses."""
    response = groq_client.chat.completions.create(
        model=MODEL, temperature=0, max_tokens=1000,
        messages=[
            {"role": "system", "content":
             "Interpret the requested course/domain scope using only the supplied real database catalog. "
             "Return JSON only with keys mode, exact_pair, matched_domain, domain_pairs, difficulty_order, difficulty_basis. "
             "mode must be exact, domain, clarify, or unknown. exact_pair is one exact {vertical, course, course_level} entry or null. "
             "domain_pairs is a list of exact {vertical, course, course_level} entries from catalog and should include "
             "all relevant courses for the named domain (not unrelated nearest neighbors). If an exact "
             "course title also has related subcourses and the user did not clearly say exact course or "
             "whole domain, choose clarify. If the user says domain/overall/all related, choose domain. "
             "If a term such as backend has no exact course but has a matching mapped domain, choose domain "
             "and return that exact domain label as matched_domain. Mapped domain associations are included "
             "on catalog entries; prefer those mappings over inventing a new course set. Unreviewed mappings "
             "are inferred and should not be described as verified. For a general full-stack request, use "
             "the mapped 'Full Stack Development' domain, which combines mapped frontend and backend/database "
             "courses, instead of the narrower title-only 'Full Stack & DevOps' mapping. "
             "difficulty_order lists relevant entries from easiest to hardest using the explicit course_level "
             "column where possible; titles alone are not authoritative. When ordering is unclear, preserve "
             "catalog order and label it inferred. Never invent catalog entries."},
            {"role": "user", "content":
             f"Question: {question}\nCatalog: {json.dumps(catalog, ensure_ascii=False)}"},
        ],
    )
    text = response.choices[0].message.content or "{}"
    match = re.search(r"\{.*\}", text, re.S)
    try:
        plan = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {"mode": "unknown"}
    if not isinstance(plan, dict):
        return {"mode": "unknown"}
    if not isinstance(plan.get("mode"), str) or plan["mode"] not in {"exact", "domain", "clarify", "unknown"}:
        plan["mode"] = "unknown"
    allowed = {(str(item.get("vertical")), str(item.get("course")), str(item.get("course_level", "Level 1")))
               for item in catalog}
    def valid_pairs(values):
        output = []
        for pair in values if isinstance(values, list) else []:
            if isinstance(pair, dict):
                key = (str(pair.get("vertical")), str(pair.get("course")), str(pair.get("course_level", "Level 1")))
                if key in allowed and key not in {
                    (x["vertical"], x["course"], x["course_level"]) for x in output
                }:
                    output.append({"vertical": key[0], "course": key[1], "course_level": key[2]})
        return output
    plan["exact_pair"] = valid_pairs([plan.get("exact_pair")])[0] if plan.get("exact_pair") else None
    plan["domain_pairs"] = valid_pairs(plan.get("domain_pairs"))
    plan["difficulty_order"] = valid_pairs(plan.get("difficulty_order"))
    domain_names = {
        domain.get("name")
        for item in catalog for domain in item.get("domains", [])
        if isinstance(domain, dict) and domain.get("name")
    }
    matched_domain = plan.get("matched_domain")
    plan["matched_domain"] = next((
        domain for domain in domain_names
        if isinstance(matched_domain, str) and domain.casefold() == matched_domain.strip().casefold()
    ), None)
    if plan["mode"] == "domain" and plan["matched_domain"]:
        plan["domain_pairs"] = [
            {"vertical": item["vertical"], "course": item["course"], "course_level": item["course_level"]}
            for item in catalog
            if any(domain.get("name") == plan["matched_domain"] for domain in item.get("domains", []))
        ]
    if plan["mode"] == "clarify" and (not plan["exact_pair"] or len(plan["domain_pairs"]) < 2):
        plan["mode"] = "unknown"
    return plan

def clarification_text(plan: dict) -> str:
    exact = f"{plan['exact_pair']['course']} ({plan['exact_pair']['course_level']})"
    domain_courses = ", ".join(dict.fromkeys(
        f"{item['course']} ({item['course_level']})" for item in plan["domain_pairs"]
    ))
    return (
        f'I found both the exact course "{exact}" and related courses. Which scope should I use?\n\n'
        f"*1* - Only the exact course: {exact}\n"
        f"*2* - The wider domain: {domain_courses}"
    )

def execute_domain_ranking(question: str, plan: dict, structured: bool = False) -> str | dict:
    pairs = plan.get("domain_pairs", [])
    if not pairs:
        answer = "I couldn't find related courses for that domain in the course catalog."
        return {"answer": answer, "columns": [], "rows": []} if structured else answer
    where = " OR ".join(
        "(BTRIM(vertical_name) = %s AND BTRIM(course_name) = %s AND course_level = %s)"
        for _ in pairs
    )
    pair_params = [value for pair in pairs for value in
                   (pair["vertical"], pair["course"], pair["course_level"])]
    level_score = "(COALESCE((regexp_match(course_level, '(?i)^Level\\s*([0-9]+(?:\\.[0-9]+)?)([A-Z]?)$'))[1]::NUMERIC, 1) * 100 + CASE WHEN COALESCE((regexp_match(course_level, '(?i)^Level\\s*[0-9]+(?:\\.[0-9]+)?([A-Z]?)$'))[1], '') = '' THEN 0 ELSE ASCII(UPPER((regexp_match(course_level, '(?i)^Level\\s*[0-9]+(?:\\.[0-9]+)?([A-Z]?)$'))[1])) - 64 END)"
    limit_match = re.search(r"\btop\s+(\d+)\b", question, re.I)
    limit = min(int(limit_match.group(1)), 100) if limit_match else 10
    sql = (
        "SELECT roll_number, name, COUNT(DISTINCT (LOWER(BTRIM(course_name)), course_level)) AS levels_cleared, "
        f"SUM({level_score}) AS difficulty_score, MAX({level_score}) AS highest_level_score, "
        "STRING_AGG(DISTINCT BTRIM(course_name) || ' (' || course_level || ')', ', ' "
        "ORDER BY BTRIM(course_name) || ' (' || course_level || ')') AS cleared_courses "
        "FROM ps_student_course_summary WHERE cleared IS TRUE AND (" + where + ") "
        "GROUP BY roll_number, name ORDER BY difficulty_score DESC, levels_cleared DESC, "
        "highest_level_score DESC, name "
        f"LIMIT {limit}"
    )
    sql = validate_sql(sql)
    cols, rows = fetch_rows(sql, tuple(pair_params))
    result = [dict(zip(cols, [str(value) if value is not None else None for value in row])) for row in rows]
    answer = (
        f"Top {limit} domain students, ranked by summed numeric course levels, then number of distinct levels cleared. "
        "The level labels are taken from course names; difficulty itself is not a stored field."
    ) if result else "No students have cleared courses matched to this domain."
    return {"answer": answer, "columns": cols, "rows": result} if structured else answer

def generate_sql(
    question: str, schema: list[dict], candidates: list[dict], correction: str = "",
    scope_plan: dict | None = None,
) -> str:
    context = json.dumps({
        "columns": schema,
        "related_course_candidates": candidates,
        "selected_scope": scope_plan,
    }, ensure_ascii=False)
    system_content = (
        "Translate questions about student course records into PostgreSQL. Use the supplied live schema "
        "and semantic candidates to understand the request; candidates are hints, not answer data. "
        "When selected_scope is exact, filter to its exact vertical/course pair. When selected_scope is domain, "
        "include only its listed domain_pairs; do not broaden the course set. "
        "Do not require a course name for an overall question. A skill is not a source column, but when "
        "selected_scope supplies database-matched domain_pairs, use those exact pairs as the domain's "
        "course mapping. A department without mapped values cannot be assumed. For student counts use "
        "COUNT(DISTINCT roll_number). "
        "For rankings group by requested student identity and order by the requested metric. "
        "Output only one executable SELECT statement. Do not use joins, other tables, system catalogs, "
        "side effects, or writes. Use only the approved table and live columns. Limit row lists to 100.\n\n"
        + STATIC_SCHEMA
    )
    response = groq_client.chat.completions.create(
        model=MODEL, temperature=0, max_tokens=900,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content":
             f"Live database context: {context}\n\nQuestion: {question}"
             + (f"\n\nYour previous SQL failed. Correct it using this PostgreSQL error: {correction}" if correction else "")},
        ],
    )
    return validate_sql(response.choices[0].message.content or "")

def execute_and_answer(
    question: str, structured: bool = False, scope_plan: dict | None = None
) -> str | dict:
    if scope_plan and scope_plan.get("mode") == "domain" and re.search(
        r"\b(top|rank|highest|best|most)\b", question, re.I
    ):
        return execute_domain_ranking(question, scope_plan, structured)
    schema = database_schema()
    candidates = semantic_candidates(question)
    correction = ""
    for attempt in range(2):
        sql = generate_sql(question, schema, candidates, correction, scope_plan)
        try:
            cols, rows = fetch_rows(sql)
            break
        except Exception as error:
            if attempt:
                raise
            correction = f"{type(error).__name__}: {error}"
    if not rows:
        answer = "No records matched that question in the available student course data."
        return {"answer": answer, "columns": [], "rows": []} if structured else answer
    result = [dict(zip(cols, [str(value) if value is not None else None for value in row])) for row in rows]
    response = groq_client.chat.completions.create(
        model=MODEL, temperature=0.1, max_tokens=500,
        messages=[
            {"role": "system", "content":
             "Answer using only the SQL result and supplied schema. Give the direct answer first, "
             "include the unit/metric (students vs attempts), and be concise. If the requested concept "
             "is absent from schema/results, say so and identify the closest available fields. "
             "Never infer facts not present in the result."},
            {"role": "user", "content":
             f"Question: {question}\nSchema: {json.dumps(schema)}\nSQL: {sql}\n"
             f"Result: {json.dumps(result, ensure_ascii=False)}"},
        ],
    )
    answer = response.choices[0].message.content or json.dumps(result, ensure_ascii=False)
    return {"answer": answer, "columns": cols, "rows": result} if structured else answer

def answer_question(
    sender: str, question: str, structured: bool = False,
    clarification_context: dict | None = None,
) -> str | dict:
    clean = question.strip()
    pending = clarification_context if structured else USER_SESSIONS.get(sender)
    if pending:
        selected = None
        if re.fullmatch(r"\s*(?:option\s*)?1\s*", clean, re.I) or clean.lower() in {"exact", "exact course", "course"}:
            selected = "exact"
        elif re.fullmatch(r"\s*(?:option\s*)?2\s*", clean, re.I) or clean.lower() in {"domain", "all related", "whole domain"}:
            selected = "domain"
        if selected:
            USER_SESSIONS.pop(sender, None)
            plan = dict(pending)
            if selected == "exact":
                plan.update({"mode": "exact", "domain_pairs": [plan["exact_pair"]]})
            else:
                plan["mode"] = "domain"
            return execute_and_answer(plan["original_question"], structured, plan)
        USER_SESSIONS.pop(sender, None)

    broad_question = bool(re.search(
        r"\b(overall|across all courses|in all courses|all courses|every course)\b", clean, re.I
    )) and not bool(re.search(r"\b(?:in|for|named|called)\s+[a-z0-9]", clean, re.I))
    candidates = semantic_candidates(clean) if not broad_question else []
    catalog = add_domain_mappings(relevant_catalog(clean, candidates)) if not broad_question else []
    if catalog:
        plan = interpret_scope(clean, catalog)
        if plan.get("mode") == "clarify":
            plan["original_question"] = clean
            answer = clarification_text(plan)
            if structured:
                return {"answer": answer, "columns": [], "rows": [], "clarification": plan}
            USER_SESSIONS[sender] = plan
            return answer
        if plan.get("mode") in {"exact", "domain"}:
            plan["original_question"] = clean
            return execute_and_answer(clean, structured, plan)
    return execute_and_answer(clean, structured)
async def send_whatsapp(to: str, body: str):
    if not (WA_TOKEN and PHONE_ID):
        raise RuntimeError("WhatsApp Cloud API credentials missing")
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": to, "type": "text", "text": {"preview_url": False, "body": body[:4000]}
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers={"Authorization": f"Bearer {WA_TOKEN}"}, json=payload)
        r.raise_for_status()

class QueryRequest(BaseModel):
    question: str
    session_id: str
    clarification_context: dict | None = None


@app.get("/")
def frontend():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/health")
def health():
    try:
        fetch_rows("SELECT 1")
    except Exception as error:
        print(f"Database health check failed: {type(error).__name__}: {error}")
        raise HTTPException(status_code=503, detail="Database unavailable.") from error
    return {"status": "ok", "database": "connected", "service": "Student Skills Query Bot"}


@app.post("/api/query")
def query(request: QueryRequest):
    question = request.question.strip()
    session_id = request.session_id.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if not session_id:
        raise HTTPException(status_code=400, detail="Session ID is required.")
    try:
        result = answer_question(
            session_id, question, structured=True,
            clarification_context=request.clarification_context,
        )
        if isinstance(result, dict):
            return result
        return {"answer": result, "columns": [], "rows": []}
    except Exception as error:
        print(f"Frontend query failed: {type(error).__name__}: {error}")
        raise HTTPException(
            status_code=500,
            detail="The question could not be processed. Please try again.",
        ) from error

@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Webhook verification failed")

@app.post("/webhook")
async def receive_webhook(request: Request):
    body = await request.json()
    try:
        value = body["entry"][0]["changes"][0]["value"]
        messages = value.get("messages", [])
        if not messages:
            return {"status": "ignored"}
        msg = messages[0]
        sender = msg.get("from")
        text_body = msg.get("text", {}).get("body", "").strip()
        if not sender or not text_body:
            return {"status": "ignored"}
        try:
            answer = answer_question(sender, text_body)
        except Exception as e:
            print(f"Question handling failed: {type(e).__name__}: {e}")
            answer = "I couldn't process that query. Please try asking again."
        await send_whatsapp(sender, answer)
    except Exception as e:
        print(f"Webhook processing failed: {type(e).__name__}: {e}")
    return {"status": "received"}
