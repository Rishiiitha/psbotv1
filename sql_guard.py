import re

ALLOWED_COLUMNS = {
    "roll_number", "name", "vertical_name", "course_name", "course_level",
    "date", "start_time", "end_time", "venue_name", "status", "attempt_count",
    "cleared", "first_attempt_date", "last_attempt_date"
}
ALLOWED_SOURCES = {
    "ps_cleared_course_view",
    "ps_cleared_course_view_cleaned",
    "ps_student_course_summary",
}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|execute|do|merge|refresh|vacuum|analyze|set|reset|join|union|intersect|except|"
    r"pg_sleep|information_schema|pg_catalog)\b", re.I
)

def validate_sql(sql: str) -> str:
    sql = sql.strip()
    
    # 1. Clean markdown code fences if present
    sql = re.sub(r"```(?:sql)?", "", sql, flags=re.I).strip()
    sql = re.sub(r"```$", "", sql).strip()
    
    # 2. Extract starting from SELECT or WITH, removing any LLM preamble
    match = re.search(r"\b(SELECT|WITH)\b.*", sql, re.I | re.DOTALL)
    if not match:
        raise ValueError("Only SELECT queries are allowed.")
    
    sql = match.group(0).strip()

    # 3. Handle trailing semicolons
    if ";" in sql.rstrip(";"):
        sql = sql.split(";")[0].strip()
    else:
        sql = sql.rstrip(";")

    # 4. Security checks
    if FORBIDDEN.search(sql):
        raise ValueError("Query contains a forbidden operation or system catalog.")
    sources = re.findall(r"\bfrom\s+([a-zA-Z_][\w.]*)", sql, re.I)
    if not sources or any(source.lower() not in ALLOWED_SOURCES for source in sources):
        raise ValueError("Query must read only from the approved dataset.")
    
    # 5. Ensure safe row LIMIT
    if not re.search(r"\blimit\s+\d+\b", sql, re.I):
        sql += " LIMIT 50"
    m = re.search(r"\blimit\s+(\d+)\b", sql, re.I)
    if m and int(m.group(1)) > 100:
        sql = sql[:m.start(1)] + "100" + sql[m.end(1):]

    return sql
