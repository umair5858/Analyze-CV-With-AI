import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pypdf

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "database.db"

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
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            description TEXT NOT NULL,
            recruiter_email TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            candidate_name TEXT NOT NULL,
            candidate_email TEXT NOT NULL,
            cv_text TEXT NOT NULL,
            match_score INTEGER NOT NULL,
            status TEXT DEFAULT 'Pending'
        )
    """)
    conn.commit()
    conn.close()

# Modern Lifespan Event Handler (Startup error fix)
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Career Hub", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuthModel(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str

class JobModel(BaseModel):
    title: str
    company: str
    description: str
    recruiter_email: str

class ApplyModel(BaseModel):
    job_id: int
    candidate_name: str
    candidate_email: str
    cv_text: str
    match_score: int

# HTML Pages Routes
@app.get("/")
def home():
    file_path = BASE_DIR / "signup.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="signup.html file missing in project folder!")
    return FileResponse(file_path)

@app.get("/signup")
def signup_page():
    file_path = BASE_DIR / "signup.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="signup.html file missing in project folder!")
    return FileResponse(file_path)

@app.get("/login")
def login_page():
    file_path = BASE_DIR / "login.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="login.html file missing in project folder!")
    return FileResponse(file_path)

@app.get("/dashboard")
def dashboard_page():
    file_path = BASE_DIR / "dashboard.html"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="dashboard.html file missing in project folder!")
    return FileResponse(file_path)

# APIs
@app.post("/api/signup")
def signup(data: AuthModel):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (full_name, email, password, role) VALUES (?, ?, ?, ?)",
            (data.full_name.strip() if data.full_name else "", data.email.lower().strip(), data.password, data.role.lower().strip())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Email is already registered!")
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    conn.close()
    return {"status": "ok"}

@app.post("/api/login")
def login(data: AuthModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT full_name, email, role FROM users WHERE email=? AND password=?",
        (data.email.lower().strip(), data.password)
    )
    user = cursor.fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Email or Password!")
    if user["role"] != data.role.lower().strip():
        raise HTTPException(status_code=401, detail=f"Account registered as {user['role'].capitalize()}")
    return {"status": "ok", "user": dict(user)}

@app.post("/api/parse-pdf")
async def parse_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported!")
    try:
        reader = pypdf.PdfReader(file.file)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return {"text": text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF Parsing Error: {str(e)}")

@app.post("/api/jobs")
def post_job(data: JobModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO jobs (title, company, description, recruiter_email) VALUES (?, ?, ?, ?)",
        (data.title, data.company, data.description, data.recruiter_email)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/jobs")
def get_jobs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs ORDER BY id DESC")
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jobs

@app.post("/api/apply")
def apply_job(data: ApplyModel):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO applications (job_id, candidate_name, candidate_email, cv_text, match_score) VALUES (?, ?, ?, ?, ?)",
        (data.job_id, data.candidate_name, data.candidate_email, data.cv_text, data.match_score)
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/api/applications")
def get_applications(email: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.id, a.candidate_name, a.candidate_email, a.cv_text, a.match_score, a.status, j.title as job_title
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE j.recruiter_email = ?
        ORDER BY a.id DESC
    """, (email,))
    apps = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return apps