import os
import json
import sqlite3
import pdfplumber
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai

# Setup Gemini API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

app = FastAPI()

# Serve static files if needed (CSS, JS, Images)
# app.mount("/static", StaticFiles(directory="static"), name="static")

DB_FILE = "portal.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            city TEXT DEFAULT '',
            bio TEXT DEFAULT ''
        )
    ''')

    # Jobs Table (Includes city)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recruiter_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            city TEXT NOT NULL,
            salary TEXT NOT NULL,
            job_type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (recruiter_id) REFERENCES users (id)
        )
    ''')

    # Applications Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            match_score TEXT NOT NULL,
            score_percentage INTEGER DEFAULT 0,
            feedback TEXT NOT NULL,
            matching_skills TEXT NOT NULL,
            missing_skills TEXT NOT NULL,
            resume_text TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE,
            FOREIGN KEY (candidate_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# =========================================================
# FRONTEND HTML PAGE ROUTES
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    return FileResponse("templates/login.html") # ya signup.html jo aap ka main page ho

@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    return FileResponse("templates/login.html")

@app.get("/signup", response_class=HTMLResponse)
async def serve_signup():
    return FileResponse("templates/signup.html")

@app.get("/recruiter/dashboard", response_class=HTMLResponse)
async def serve_recruiter_dashboard():
    return FileResponse("templates/recruiter_dashboard.html")

@app.get("/candidate/dashboard", response_class=HTMLResponse)
async def serve_candidate_dashboard():
    return FileResponse("templates/candidate_dashboard.html")

# =========================================================
# AUTH API ROUTES
# =========================================================

@app.post("/api/signup")
async def signup(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
            (name, email, password, role)
        )
        conn.commit()
        return {"message": "Account created successfully!"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    finally:
        conn.close()

@app.post("/api/login")
async def login(email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user_data = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "city": user["city"],
        "bio": user["bio"]
    }
    redirect_url = "/recruiter/dashboard" if user["role"] == "recruiter" else "/candidate/dashboard"
    return {"user": user_data, "redirect_url": redirect_url}

# =========================================================
# PROFILE MANAGEMENT
# =========================================================

@app.post("/api/candidate/profile")
async def update_profile(
    candidate_id: int = Form(...),
    name: str = Form(...),
    city: str = Form(...),
    bio: str = Form(...)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET name = ?, city = ?, bio = ? WHERE id = ?",
        (name, city, bio, candidate_id)
    )
    conn.commit()
    conn.close()
    return {"message": "Profile updated successfully!"}

# =========================================================
# JOBS CRUD & CITY SEARCH
# =========================================================

@app.get("/api/jobs")
async def get_jobs(city: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    if city and city.strip():
        cursor.execute("SELECT * FROM jobs WHERE LOWER(city) LIKE LOWER(?) ORDER BY id DESC", (f"%{city.strip()}%",))
    else:
        cursor.execute("SELECT * FROM jobs ORDER BY id DESC")
    jobs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"jobs": jobs}

@app.post("/api/jobs")
async def create_job(
    recruiter_id: int = Form(...),
    title: str = Form(...),
    company: str = Form(...),
    city: str = Form(...),
    salary: str = Form(...),
    job_type: str = Form(...),
    description: str = Form(...)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''INSERT INTO jobs 
           (recruiter_id, title, company, city, salary, job_type, description, created_at) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (recruiter_id, title, company, city, salary, job_type, description, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return {"message": "Job posted successfully!"}

@app.put("/api/jobs/{job_id}")
async def update_job(
    job_id: int,
    title: str = Form(...),
    company: str = Form(...),
    city: str = Form(...),
    salary: str = Form(...),
    job_type: str = Form(...),
    description: str = Form(...)
):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''UPDATE jobs 
           SET title=?, company=?, city=?, salary=?, job_type=?, description=? 
           WHERE id=?''',
        (title, company, city, salary, job_type, description, job_id)
    )
    conn.commit()
    conn.close()
    return {"message": "Job updated successfully!"}

@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    cursor.execute("DELETE FROM applications WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()
    return {"message": "Job deleted successfully!"}

# =========================================================
# AI MATCHING & APPLICATIONS
# =========================================================

@app.post("/api/apply")
async def apply_job(
    job_id: int = Form(...),
    candidate_id: int = Form(...),
    resume: UploadFile = File(...)
):
    extracted_text = ""
    try:
        with pdfplumber.open(resume.file) as pdf:
            for page in pdf.pages:
                extracted_text += page.extract_text() or ""
    except Exception:
        raise HTTPException(status_code=400, detail="Could not extract text from PDF.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT description FROM jobs WHERE id = ?", (job_id,))
    job = cursor.fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Job not found.")

    prompt = f"""
    Analyze the candidate resume against the job description.
    
    Job Description:
    {job['description']}

    Resume Text:
    {extracted_text}

    Return ONLY a valid JSON format:
    {{
        "score_percentage": (integer 0-100),
        "short_summary": "2-3 concise lines describing fit for the role",
        "matching_skills": ["skill1", "skill2"],
        "missing_skills": ["gap1", "gap2"]
    }}
    """

    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        cleaned_json = response.text.strip().replace("```json", "").replace("```", "")
        ai_data = json.loads(cleaned_json)
    except Exception:
        ai_data = {
            "score_percentage": 65,
            "short_summary": "Candidate meets basic requirements but requires improvement in core technical skills.",
            "matching_skills": ["Communication", "Domain Knowledge"],
            "missing_skills": ["Advanced Frameworks", "System Design"]
        }

    cursor.execute('''
        INSERT INTO applications 
        (job_id, candidate_id, match_score, score_percentage, feedback, matching_skills, missing_skills, resume_text, applied_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        job_id,
        candidate_id,
        f"{ai_data['score_percentage']}%",
        ai_data["score_percentage"],
        ai_data["short_summary"],
        json.dumps(ai_data["matching_skills"]),
        json.dumps(ai_data["missing_skills"]),
        extracted_text,
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

    return {
        "message": "Application submitted successfully!",
        "analysis": ai_data
    }

@app.get("/api/applications/{job_id}")
async def get_applications(job_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            a.*,
            j.title AS job_title,
            j.company AS company_name,
            j.city AS location,
            u.id AS candidate_id,
            u.name AS candidate_name,
            u.email AS candidate_email,
            u.city AS candidate_city,
            u.bio AS candidate_description
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        JOIN users u ON a.candidate_id = u.id
        WHERE a.job_id = ?
        ORDER BY a.id DESC
    ''', (job_id,))

    app_rows = cursor.fetchall()
    conn.close()

    result = []
    for app_row in app_rows:
        try:
            matching_skills = json.loads(app_row["matching_skills"])
        except Exception:
            matching_skills = []

        try:
            missing_skills = json.loads(app_row["missing_skills"])
        except Exception:
            missing_skills = []

        result.append({
            "id": app_row["id"],
            "job_id": app_row["job_id"],
            "job_title": app_row["job_title"],
            "company_name": app_row["company_name"],
            "location": app_row["location"],
            "candidate_id": app_row["candidate_id"],
            "candidate_name": app_row["candidate_name"],
            "candidate_email": app_row["candidate_email"],
            "candidate_city": app_row["candidate_city"] or "",
            "candidate_description": app_row["candidate_description"] or "",
            "score_percentage": app_row["score_percentage"] or 0,
            "match_score": app_row["match_score"] or "",
            "feedback": app_row["feedback"] or "",
            "matching_skills": matching_skills,
            "missing_skills": missing_skills,
            "resume_text": app_row["resume_text"] or "",
            "applied_at": app_row["applied_at"]
        })

    return {
        "applications": result
    }

# =========================================================
# HEALTH CHECK & RUN SERVER
# =========================================================

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "application": "Career Hub",
        "database": "SQLite",
        "time": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )