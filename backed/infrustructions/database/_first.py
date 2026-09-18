import os
import sqlite3


BASE_DIR:str=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH=os.path.join(BASE_DIR,"data","data.db")

def get_connection():
    db_dir=os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir,exist_ok=True)

    conn=sqlite3.connect(DB_PATH)
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    conn=get_connection()
    try:
        cursor=conn.cursor()
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                type        TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                amount      REAL NOT NULL CHECK(amount > 0),
                category    TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
        )"""
        )
    except sqlite3.Error as e:
        print(f"Error initializing database: {e}")
    finally:
        conn.close()
