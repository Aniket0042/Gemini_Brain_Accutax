import psycopg2

try:
    conn = psycopg2.connect(host='127.0.0.1', port=5435, dbname='accutax_bk_1_5', user='postgres', password='12345678')
    cur = conn.cursor()
    
    emails = ['admin@accutax.com', 'user_single@example.com', 'user_multi@example.com', 'user_no_org@example.com']
    
    print("\n--- CHECKING SPECIFIC USERS ---")
    cur.execute("SELECT id, email FROM users WHERE email IN %s;", (tuple(emails),))
    users = cur.fetchall()
    for u in users:
        print(u)
        
    print("\n--- INSERTING OR UPDATING USERS ---")
    # If users do not exist, we will insert them
    inserted_users = []
    for email in emails:
        cur.execute("SELECT id FROM users WHERE email = %s;", (email,))
        row = cur.fetchone()
        if not row:
            print(f"Inserting {email}...")
            # We don't know the exact schema of users table, but let's try a minimal insert
            # We need to know the required columns. 
            pass

    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
