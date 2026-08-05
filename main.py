import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

app = FastAPI(title="Career Hub Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "database.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

# --- Schemas ---
class AuthModel(BaseModel):
    email: EmailStr
    password: str
    role: str

# --- Page Routes ---
@app.get("/")
def home_page():
    return FileResponse("signup.html")

@app.get("/signup")
def signup_page():
    return FileResponse("signup.html")

@app.get("/login")
def login_page():
    return FileResponse("login.html")

@app.get("/dashboard")
def dashboard_page():
    return FileResponse("index.html")

# --- APIs ---
@app.post("/api/signup")
def signup(data: AuthModel):
    conn = get_db()
    cursor = conn.cursor()
    clean_email = data.email.lower().strip()
    clean_role = data.role.lower().strip()

    cursor.execute("SELECT id FROM users WHERE email = ?", (clean_email,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Account with this email already exists.")

    cursor.execute(
        "INSERT INTO users (email, password, role) VALUES (?, ?, ?)",
        (clean_email, data.password, clean_role)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Account created successfully!"}

@app.post("/api/login")
def login(data: AuthModel):
    conn = get_db()
    cursor = conn.cursor()
    clean_email = data.email.lower().strip()
    clean_role = data.role.lower().strip()

    cursor.execute(
        "SELECT id, email, role FROM users WHERE email = ? AND password = ? AND role = ?",
        (clean_email, data.password, clean_role)
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid Email, Password, or Role selected.")

    return {
        "status": "success",
        "message": "Login successful!",
        "user": {"email": user["email"], "role": user["role"]}
    }