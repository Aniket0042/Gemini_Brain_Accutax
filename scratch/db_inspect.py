import psycopg2

try:
    conn = psycopg2.connect(host='127.0.0.1', port=5435, dbname='accutax_bk_1_5', user='postgres', password='12345678')
    cur = conn.cursor()

    print("--- USER_ORGANIZATIONS SCHEMA ---")
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'user_organizations';")
    print(cur.fetchall())
    
    print("\n--- USERS SCHEMA ---")
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'users';")
    print(cur.fetchall())

    print("\n--- USERS ---")
    cur.execute("SELECT id, email FROM users;")
    users = cur.fetchall()
    for u in users:
        print(u)
        
    print("\n--- USER_ORGANIZATIONS ---")
    cur.execute("SELECT * FROM user_organizations;")
    for uo in cur.fetchall():
        print(uo)
        
    print("\n--- ORGANIZATIONS ---")
    cur.execute("SELECT id, name FROM organizations LIMIT 10;")
    for org in cur.fetchall():
        print(org)

    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
