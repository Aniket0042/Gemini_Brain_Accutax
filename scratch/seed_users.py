import psycopg2
from datetime import datetime
import bcrypt

try:
    conn = psycopg2.connect(host='127.0.0.1', port=5435, dbname='accutax_bk_1_5', user='postgres', password='12345678')
    cur = conn.cursor()
    
    users_data = [
        ('admin@accutax.com', [14, 44]),  
        ('user_single@example.com', [14]),
        ('user_multi@example.com', [14, 44]),
        ('user_no_org@example.com', [])
    ]
    
    hashed_password = bcrypt.hashpw('TestPass123!'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    for email, orgs in users_data:
        cur.execute("SELECT id FROM users WHERE email = %s;", (email,))
        row = cur.fetchone()
        
        if not row:
            print(f"Inserting user {email}...")
            cur.execute("SELECT MAX(id) FROM users;")
            max_id = cur.fetchone()[0] or 0
            new_id = max_id + 1
            
            try:
                cur.execute(
                    """
                    INSERT INTO users (id, email, name, password, email_verified, mfa_enabled, image_url, phone_number, eid_number, license_number, mfa_secret) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (new_id, email, 'Test User', hashed_password, True, False, '', '', '', '', '')
                )
                conn.commit()
                row = (new_id,)
            except Exception as e:
                print(f"Failed to insert {email}: {e}")
                conn.rollback()
                continue
                
        user_id = row[0]
        
        # Now insert/update user_organizations
        cur.execute("DELETE FROM user_organizations WHERE user_id = %s;", (user_id,))
        for org_id in orgs:
            cur.execute("SELECT MAX(id) FROM user_organizations;")
            max_uo_id = cur.fetchone()[0] or 0
            new_uo_id = max_uo_id + 1
            print(f"Assigning user {email} (id {user_id}) to org {org_id}...")
            cur.execute("INSERT INTO user_organizations (id, user_id, organization_id, role, created_at) VALUES (%s, %s, %s, %s, %s);", 
                        (new_uo_id, user_id, org_id, 'member', datetime.now()))
        
        conn.commit()

    cur.close()
    conn.close()
    print("Done setting up users.")
except Exception as e:
    print(f"Error: {e}")
