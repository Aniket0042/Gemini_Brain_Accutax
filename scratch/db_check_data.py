import psycopg2

try:
    conn = psycopg2.connect(host='127.0.0.1', port=5435, dbname='accutax_bk_1_5', user='postgres', password='12345678')
    cur = conn.cursor()
    
    print("--- langchain_pg_embedding SCHEMA ---")
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'langchain_pg_embedding';")
    print(cur.fetchall())

    print("--- rag_document_embeddings SCHEMA ---")
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'rag_document_embeddings';")
    print(cur.fetchall())
    
    print("--- documents SCHEMA ---")
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'documents';")
    print(cur.fetchall())
    
    print("--- DATA SAMPLES ---")
    cur.execute("SELECT id, organization_id, metadata FROM langchain_pg_embedding LIMIT 5;")
    for row in cur.fetchall():
        print(row)

    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
