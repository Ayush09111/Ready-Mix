import sqlite3

# Your database file name is set here
DB_NAME = 'rmc_erp_system.db'

# Connect to the SQLite database
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

print("--- DATABASE SCHEMA (CREATE TABLE statements) ---")
# Query to get all table creation statements
cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()

for table_name, sql_code in tables:
    print(f"\n--- Table: {table_name} ---")
    print(sql_code)
    
    print(f"\n--- Data in '{table_name}' (first 5 rows) ---")
    # Query to get data from the current table
    try:
        # Using f-string safely here as table_name comes from the database itself
        cursor.execute(f"SELECT * FROM \"{table_name}\" LIMIT 5;")
        rows = cursor.fetchall()
        if rows:
            # Print column headers
            column_names = [description[0] for description in cursor.description]
            print(column_names)
            # Print rows
            for row in rows:
                print(row)
        else:
            print("No data in this table.")
    except Exception as e:
        print(f"Could not fetch data: {e}")

# Close the connection
conn.close()