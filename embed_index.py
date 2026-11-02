"""Optional pgvector semantic index for course/vertical/level names.
Requires sentence-transformers and the course_embeddings table from schema.sql.
"""
from sentence_transformers import SentenceTransformer
from db import connect

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

def main():
    model = SentenceTransformer(MODEL_NAME)  # downloads model on first run
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT vertical_name, course_name, course_level
                FROM ps_cleared_course_view_cleaned
            """)
            pairs = cur.fetchall()
        batch=[]
        for vertical, course, level in pairs:
            text=f"Vertical: {vertical}. Course or skill: {course}. Course level: {level}"
            batch.append((vertical, course, level, text))
        texts=[x[3] for x in batch]
        vectors=model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
        with conn.cursor() as cur:
            for i, (vertical, course, level, _) in enumerate(batch):
                vector = "[" + ",".join(f"{float(v):.8f}" for v in vectors[i]) + "]"
                cur.execute("""
                    INSERT INTO course_embeddings(vertical_name, course_name, course_level, embedding)
                    VALUES (%s,%s,%s,%s::vector)
                    ON CONFLICT (vertical_name, course_name, course_level)
                    DO UPDATE SET embedding=EXCLUDED.embedding
                """, (vertical, course, level, vector))
                if i and i % 500 == 0:
                    print(f"Indexed {i:,}/{len(batch):,}")
    print(f"Indexed {len(batch):,} distinct course/vertical/level pairs.")

if __name__ == "__main__":
    main()
