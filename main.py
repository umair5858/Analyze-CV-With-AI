import os
import json
import sqlite3
import hashlib
import secrets
from typing import Optional

from fastapi import (
    FastAPI,
    Request,
    Form,
    File,
    UploadFile,
    HTTPException,
    Query
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import pypdf

try:
    import google.generativeai as genai
except ImportError:
    genai = None


app = FastAPI(title="Career Hub | Smart Career Platform")

# ============================================================
# GEMINI AI
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

ai_configured = False

if genai and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_configured = True
        print("Gemini AI configured successfully.")
    except Exception as e:
        print("Gemini configuration error:", e)
else:
    print("Gemini AI not configured. Local analyzer will be used.")


# ============================================================
# DATABASE
# ============================================================

DB_NAME = "portal.db"


def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'candidate'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company_name TEXT NOT NULL,
            salary TEXT NOT NULL,
            experience TEXT NOT NULL,
            job_type TEXT NOT NULL DEFAULT 'Full-time',
            location TEXT NOT NULL DEFAULT 'Remote',
            description TEXT NOT NULL DEFAULT '',
            requirements TEXT NOT NULL,
            posted_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (posted_by) REFERENCES users (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,
            candidate_name TEXT NOT NULL,
            candidate_email TEXT NOT NULL,
            match_score TEXT NOT NULL,
            score_percentage INTEGER NOT NULL,
            feedback TEXT NOT NULL,
            missing_skills TEXT NOT NULL,
            resume_text TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (job_id) REFERENCES jobs (id),
            FOREIGN KEY (candidate_id) REFERENCES users (id)
        )
    """)

    # --------------------------------------------------------
    # Existing database migration
    # --------------------------------------------------------

    columns = [
        row[1]
        for row in cursor.execute(
            "PRAGMA table_info(applications)"
        ).fetchall()
    ]

    if "resume_text" not in columns:
        cursor.execute(
            "ALTER TABLE applications ADD COLUMN resume_text TEXT DEFAULT ''"
        )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TEMPLATES / STATIC
# ============================================================

if os.path.isdir("static"):
    app.mount(
        "/static",
        StaticFiles(directory="static"),
        name="static"
    )

templates = Jinja2Templates(directory="templates")


# ============================================================
# PASSWORD HELPERS
# ============================================================

def hash_password(password: str) -> str:

    salt = secrets.token_hex(16)

    hashed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    )

    return f"{salt}${hashed.hex()}"


def verify_password(password: str, stored_password: str) -> bool:

    # Support old accounts that were saved as plain text
    if "$" not in stored_password:
        return password == stored_password

    try:
        salt, stored_hash = stored_password.split("$", 1)

        hashed = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000
        )

        return secrets.compare_digest(
            hashed.hex(),
            stored_hash
        )

    except Exception:
        return False


# ============================================================
# PDF READER
# ============================================================

def extract_text_from_pdf_file(upload_file: UploadFile) -> str:

    try:

        reader = pypdf.PdfReader(upload_file.file)

        text = ""

        for page in reader.pages:

            extracted = page.extract_text()

            if extracted:
                text += extracted + "\n"

        return text.strip()

    except Exception as e:

        print("PDF reading error:", e)

        return ""


# ============================================================
# RESUME ANALYZER
# ============================================================

def analyze_resume_against_job(
    resume_text: str,
    job_title: str,
    requirements: str
) -> dict:

    if ai_configured:

        prompt = f"""
You are an expert recruitment ATS system.

Compare this candidate resume against this job.

JOB TITLE:
{job_title}

JOB REQUIREMENTS:
{requirements}

CANDIDATE RESUME:
{resume_text}

Return ONLY valid JSON:

{{
    "match_score": "High",
    "score_percentage": 0,
    "feedback": "2-3 sentence professional evaluation",
    "missing_skills": ["skill1", "skill2"]
}}

score_percentage must be between 0 and 100.
missing_skills should contain maximum 5 skills.
"""

        try:

            model = genai.GenerativeModel(
                "gemini-1.5-flash"
            )

            response = model.generate_content(prompt)

            raw = response.text.strip()

            if raw.startswith("```json"):
                raw = raw[7:]

            if raw.startswith("```"):
                raw = raw[3:]

            if raw.endswith("```"):
                raw = raw[:-3]

            result = json.loads(raw.strip())

            return result

        except Exception as e:

            print("Gemini failed:", e)


    # ========================================================
    # LOCAL FALLBACK
    # ========================================================

    req_words = [
        w.strip().lower()
        for w in requirements.replace(",", " ").split()
        if len(w.strip()) > 3
    ]

    resume_lower = resume_text.lower()

    found = [
        word
        for word in req_words
        if word in resume_lower
    ]

    score = min(
        95,
        max(
            35,
            int(
                (len(found) / max(1, len(req_words)))
                * 100
            )
        )
    )

    match_level = (
        "High"
        if score >= 75
        else "Medium"
        if score >= 50
        else "Low"
    )

    missing = list(
        dict.fromkeys(
            [
                word.title()
                for word in req_words
                if word not in found
            ]
        )
    )[:5]

    return {
        "match_score": match_level,
        "score_percentage": score,
        "feedback": (
            f"Your resume was evaluated against the "
            f"{job_title} position. Your profile shows "
            f"relevant alignment with several job requirements. "
            f"Strengthening the missing skills can improve your match."
        ),
        "missing_skills": missing
    }


# ============================================================
# PAGES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="login.html"
    )


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="signup.html"
    )


@app.get(
    "/candidate/dashboard",
    response_class=HTMLResponse
)
async def candidate_dashboard_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="candidate_dashboard.html"
    )


@app.get(
    "/recruiter/dashboard",
    response_class=HTMLResponse
)
async def recruiter_dashboard_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="recruiter_dashboard.html"
    )


# ============================================================
# SIGNUP
# ============================================================

@app.post("/api/signup")
async def register_user(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("candidate")
):

    role = role.lower().strip()

    if role not in ["candidate", "recruiter"]:

        raise HTTPException(
            status_code=400,
            detail="Please select a valid account role."
        )

    if len(password) < 4:

        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 4 characters."
        )

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        password_hash = hash_password(password)

        cursor.execute(
            """
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
            """,
            (
                name.strip(),
                email.strip().lower(),
                password_hash,
                role
            )
        )

        conn.commit()

        new_id = cursor.lastrowid

        conn.close()

        return {
            "status": "success",
            "message": "Account created successfully!",
            "user": {
                "id": new_id,
                "name": name.strip(),
                "email": email.strip().lower(),
                "role": role
            }
        }

    except sqlite3.IntegrityError:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="An account with this email already exists."
        )


# ============================================================
# LOGIN
# ============================================================

@app.post("/api/login")
async def login_user(
    email: str = Form(...),
    password: str = Form(...)
):

    email = email.strip().lower()

    conn = get_db_connection()

    user = conn.execute(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    conn.close()

    if not user:

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    if not verify_password(
        password,
        user["password"]
    ):

        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    return {
        "status": "success",
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    }


# ============================================================
# JOBS
# ============================================================

@app.post("/api/jobs")
async def create_job(
    title: str = Form(...),
    company_name: str = Form(...),
    salary: str = Form(...),
    experience: str = Form(...),
    job_type: str = Form("Full-time"),
    location: str = Form("Remote"),
    description: str = Form(""),
    requirements: str = Form(...),
    posted_by: int = Form(...)
):

    conn = get_db_connection()

    cursor = conn.cursor()

    recruiter = cursor.execute(
        """
        SELECT * FROM users
        WHERE id = ? AND role = 'recruiter'
        """,
        (posted_by,)
    ).fetchone()

    if not recruiter:

        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Only recruiters can post jobs."
        )

    cursor.execute(
        """
        INSERT INTO jobs
        (
            title,
            company_name,
            salary,
            experience,
            job_type,
            location,
            description,
            requirements,
            posted_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title.strip(),
            company_name.strip(),
            salary.strip(),
            experience.strip(),
            job_type,
            location.strip(),
            description.strip(),
            requirements.strip(),
            posted_by
        )
    )

    conn.commit()

    job_id = cursor.lastrowid

    conn.close()

    return {
        "status": "success",
        "message": "Job posted successfully!",
        "job_id": job_id
    }


@app.get("/api/jobs")
async def list_jobs(
    search: Optional[str] = Query(None)
):

    conn = get_db_connection()

    if search:

        like = f"%{search}%"

        rows = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE title LIKE ?
            OR company_name LIKE ?
            OR requirements LIKE ?
            ORDER BY created_at DESC
            """,
            (
                like,
                like,
                like
            )
        ).fetchall()

    else:

        rows = conn.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY created_at DESC
            """
        ).fetchall()

    conn.close()

    return {
        "jobs": [
            dict(row)
            for row in rows
        ]
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):

    conn = get_db_connection()

    job = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,)
    ).fetchone()

    conn.close()

    if not job:

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    return {
        "job": dict(job)
    }


# ============================================================
# RECRUITER JOBS
# ============================================================

@app.get("/api/recruiter/jobs")
async def recruiter_jobs(
    recruiter_id: int = Query(...)
):

    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE posted_by = ?
        ORDER BY created_at DESC
        """,
        (recruiter_id,)
    ).fetchall()

    jobs = []

    for row in rows:

        job = dict(row)

        count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM applications
            WHERE job_id = ?
            """,
            (job["id"],)
        ).fetchone()["total"]

        job["applicant_count"] = count

        jobs.append(job)

    conn.close()

    return {
        "jobs": jobs
    }


@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: int,
    recruiter_id: int = Query(...)
):

    conn = get_db_connection()

    job = conn.execute(
        "SELECT * FROM jobs WHERE id = ?",
        (job_id,)
    ).fetchone()

    if not job or job["posted_by"] != recruiter_id:

        conn.close()

        raise HTTPException(
            status_code=403,
            detail="You are not authorized to delete this job."
        )

    conn.execute(
        "DELETE FROM applications WHERE job_id = ?",
        (job_id,)
    )

    conn.execute(
        "DELETE FROM jobs WHERE id = ?",
        (job_id,)
    )

    conn.commit()

    conn.close()

    return {
        "status": "success",
        "message": "Job deleted successfully."
    }


# ============================================================
# APPLY
# ============================================================

@app.post("/api/apply")
async def apply_to_job(
    job_id: int = Form(...),
    candidate_id: int = Form(...),
    candidate_name: str = Form(...),
    candidate_email: str = Form(...),
    resume: UploadFile = File(...)
):

    conn = get_db_connection()

    cursor = conn.cursor()

    candidate = cursor.execute(
        """
        SELECT *
        FROM users
        WHERE id = ? AND role = 'candidate'
        """,
        (candidate_id,)
    ).fetchone()

    if not candidate:

        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Only candidate accounts can apply."
        )

    job = cursor.execute(
        """
        SELECT *
        FROM jobs
        WHERE id = ?
        """,
        (job_id,)
    ).fetchone()

    if not job:

        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Job not found."
        )

    existing = cursor.execute(
        """
        SELECT *
        FROM applications
        WHERE job_id = ?
        AND candidate_id = ?
        """,
        (
            job_id,
            candidate_id
        )
    ).fetchone()

    if existing:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="You have already applied to this job."
        )

    if not resume.filename.lower().endswith(".pdf"):

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Please upload your CV as a PDF file."
        )

    resume_text = extract_text_from_pdf_file(resume)

    if not resume_text:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Could not read this PDF. Please upload a text-based PDF."
        )

    analysis = analyze_resume_against_job(
        resume_text,
        job["title"],
        job["requirements"]
    )

    cursor.execute(
        """
        INSERT INTO applications
        (
            job_id,
            candidate_id,
            candidate_name,
            candidate_email,
            match_score,
            score_percentage,
            feedback,
            missing_skills,
            resume_text
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            candidate_id,
            candidate_name,
            candidate_email,
            analysis.get("match_score", "Medium"),
            int(analysis.get("score_percentage", 50)),
            analysis.get("feedback", ""),
            json.dumps(
                analysis.get("missing_skills", [])
            ),
            resume_text
        )
    )

    conn.commit()

    conn.close()

    return {
        "status": "success",
        "message": "Application submitted successfully!",
        "analysis": analysis
    }


# ============================================================
# CANDIDATE APPLICATIONS
# ============================================================

@app.get("/api/candidate/applications")
async def candidate_applications(
    candidate_id: int = Query(...)
):

    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT
            applications.*,
            jobs.title AS job_title,
            jobs.company_name,
            jobs.salary,
            jobs.location
        FROM applications
        JOIN jobs
        ON applications.job_id = jobs.id
        WHERE candidate_id = ?
        ORDER BY applied_at DESC
        """,
        (candidate_id,)
    ).fetchall()

    conn.close()

    applications = []

    for row in rows:

        item = dict(row)

        try:
            item["missing_skills"] = json.loads(
                item["missing_skills"]
            )
        except:
            item["missing_skills"] = []

        applications.append(item)

    return {
        "applications": applications
    }


# ============================================================
# RECRUITER APPLICATIONS
# ============================================================

@app.get("/api/recruiter/applications/{job_id}")
async def job_applications(
    job_id: int
):

    conn = get_db_connection()

    rows = conn.execute(
        """
        SELECT *
        FROM applications
        WHERE job_id = ?
        ORDER BY score_percentage DESC
        """,
        (job_id,)
    ).fetchall()

    conn.close()

    applications = []

    for row in rows:

        item = dict(row)

        try:
            item["missing_skills"] = json.loads(
                item["missing_skills"]
            )
        except:
            item["missing_skills"] = []

        applications.append(item)

    return {
        "applications": applications
    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )