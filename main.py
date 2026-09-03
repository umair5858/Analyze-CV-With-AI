import os
import json
import sqlite3
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

import pdfplumber
from google import genai
from google.genai import types

from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse


# =========================================================
# APP CONFIGURATION
# =========================================================

load_dotenv()

app = FastAPI(title="Career Hub")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DB_FILE = os.path.join(BASE_DIR, "portal.db")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

gemini_client = None

if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as error:
        print("Gemini client initialization error:", error)
        gemini_client = None


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    # USERS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            city TEXT DEFAULT '',
            bio TEXT DEFAULT ''
        )
    """)

    # JOBS
    cursor.execute("""
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

            FOREIGN KEY (recruiter_id)
            REFERENCES users(id)
            ON DELETE CASCADE
        )
    """)

    # APPLICATIONS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            candidate_id INTEGER NOT NULL,

            match_score TEXT NOT NULL DEFAULT '0%',
            score_percentage INTEGER NOT NULL DEFAULT 0,

            feedback TEXT NOT NULL DEFAULT '',
            matching_skills TEXT NOT NULL DEFAULT '[]',
            missing_skills TEXT NOT NULL DEFAULT '[]',

            resume_text TEXT NOT NULL DEFAULT '',
            applied_at TEXT NOT NULL,

            FOREIGN KEY (job_id)
            REFERENCES jobs(id)
            ON DELETE CASCADE,

            FOREIGN KEY (candidate_id)
            REFERENCES users(id)
            ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def safe_json_list(value):
    """
    Safely convert a JSON string into a Python list.
    """
    try:
        data = json.loads(value)

        if isinstance(data, list):
            return data

        return []

    except Exception:
        return []


def clean_ai_json(text: str):
    """
    Clean Gemini response if it returns JSON inside markdown fences.
    """
    if not text:
        raise ValueError("Empty AI response")

    text = text.strip()

    if text.startswith("```json"):
        text = text[7:]

    elif text.startswith("```"):
        text = text[3:]

    if text.endswith("```"):
        text = text[:-3]

    return text.strip()


def normalize_ai_result(data):
    """
    Make sure AI output always has the expected structure.
    """

    score = data.get("score_percentage", 0)

    try:
        score = int(score)
    except Exception:
        score = 0

    score = max(0, min(100, score))

    feedback = str(
        data.get("feedback")
        or data.get("short_summary")
        or ""
    ).strip()

    strengths = data.get("matching_skills", [])
    improving = data.get("missing_skills", [])

    if not isinstance(strengths, list):
        strengths = []

    if not isinstance(improving, list):
        improving = []

    strengths = [
        str(item).strip()
        for item in strengths
        if str(item).strip()
    ]

    improving = [
        str(item).strip()
        for item in improving
        if str(item).strip()
    ]

    return {
        "score_percentage": score,
        "match_score": f"{score}%",
        "feedback": feedback,
        "matching_skills": strengths,
        "missing_skills": improving
    }


# =========================================================
# AI RESUME ANALYSIS
# =========================================================

def analyze_resume_against_job(resume_text, job_description):
    """
    Compare candidate CV against job description.

    AI returns:

    - score_percentage
    - professional feedback
    - matching skills / strengths
    - missing skills / skills to improve
    """

    # -----------------------------------------------------
    # FALLBACK
    # -----------------------------------------------------

    fallback = {
        "score_percentage": 0,
        "match_score": "0%",
        "feedback": (
            "AI analysis is currently unavailable. "
            "Please configure a valid GEMINI_API_KEY to receive "
            "a detailed resume-to-job evaluation."
        ),
        "matching_skills": [],
        "missing_skills": []
    }

    if not resume_text.strip():
        fallback["feedback"] = (
            "No readable text could be extracted from the uploaded CV. "
            "Please upload a text-based PDF resume."
        )
        return fallback

    if not job_description.strip():
        fallback["feedback"] = (
            "This job does not contain enough description or requirements "
            "for an accurate CV match analysis."
        )
        return fallback

    if not gemini_client:
        return fallback

    # -----------------------------------------------------
    # PROFESSIONAL AI PROMPT
    # -----------------------------------------------------

    prompt = f"""
You are a professional ATS resume evaluator and recruitment assistant.

Your task is to carefully compare the candidate's CV against the
specific job description.

IMPORTANT:
- Do NOT invent skills that are not supported by the CV or job.
- Do NOT use a generic predefined skills list.
- The "skills to improve" section must come from the actual gaps
  between this candidate's CV and this specific job.
- Evaluate technical skills, tools, frameworks, experience,
  responsibilities and relevant qualifications where applicable.
- Do not judge the candidate on personal characteristics.
- Be objective and professional.

JOB DESCRIPTION:
----------------
{job_description}

CANDIDATE CV:
-------------
{resume_text}

Return ONLY valid JSON.

Use EXACTLY this structure:

{{
    "score_percentage": 0,
    "feedback": "Professional 3-5 sentence evaluation of how well the CV matches this specific role.",
    "matching_skills": [
        "Actual relevant strength from the CV"
    ],
    "missing_skills": [
        "Actual skill or qualification the candidate should improve for this role"
    ]
}}

SCORING GUIDELINES:

90-100:
Excellent match. Candidate covers almost all important requirements.

75-89:
Strong match. Candidate meets most important requirements but has
some smaller gaps.

60-74:
Moderate match. Candidate has useful relevant experience but several
requirements need improvement.

40-59:
Weak match. Candidate has some relevant areas but important
requirements are missing.

0-39:
Very weak match. Candidate's CV has limited relevance to the role.

For "matching_skills":
Include only meaningful skills/experience actually supported by the CV
and relevant to the job.

For "missing_skills":
Include only meaningful gaps that would actually help the candidate
become a stronger applicant for THIS job.

Return maximum 8 matching skills and maximum 8 improving skills.
"""

    try:
        # Ask Gemini to return JSON directly. This avoids the common
        # problem where the model wraps JSON in markdown or adds text.
        response = gemini_client.models.generate_content(
            model="gemini-3.7-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json"
            )
        )

        raw_response = (response.text or "").strip()

        if not raw_response:
            raise ValueError("Gemini returned an empty response.")

        print("Gemini raw response:", raw_response)

        cleaned = clean_ai_json(raw_response)

        ai_data = json.loads(cleaned)

        if not isinstance(ai_data, dict):
            raise ValueError("Gemini response is not a JSON object.")

        result = normalize_ai_result(ai_data)

        # Make sure the feedback is never blank.
        if not result["feedback"]:
            result["feedback"] = (
                "The CV was analyzed successfully, but Gemini did not "
                "return a detailed feedback summary."
            )

        return result

    except Exception as error:
        # Keep the real error visible in the terminal so debugging is easy.
        print("AI analysis error:", repr(error))

        # Return a useful fallback instead of hiding the actual problem.
        fallback["feedback"] = (
            "AI analysis could not be completed for this application. "
            "Please check the server terminal for the Gemini error details."
        )

        return fallback


# =========================================================
# FRONTEND ROUTES
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return FileResponse(
        os.path.join(TEMPLATES_DIR, "login.html")
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return FileResponse(
        os.path.join(TEMPLATES_DIR, "login.html")
    )


@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return FileResponse(
        os.path.join(TEMPLATES_DIR, "signup.html")
    )


@app.get("/candidate/dashboard", response_class=HTMLResponse)
async def candidate_dashboard():
    return FileResponse(
        os.path.join(
            TEMPLATES_DIR,
            "candidate_dashboard.html"
        )
    )


@app.get("/recruiter/dashboard", response_class=HTMLResponse)
async def recruiter_dashboard():
    return FileResponse(
        os.path.join(
            TEMPLATES_DIR,
            "recruiter_dashboard.html"
        )
    )


# =========================================================
# AUTHENTICATION
# =========================================================

@app.post("/api/signup")
async def signup(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):

    role = role.strip().lower()

    if role not in ["candidate", "recruiter"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid account role."
        )

    name = name.strip()
    email = email.strip().lower()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Name is required."
        )

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Email is required."
        )

    if len(password) < 4:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 4 characters."
        )

    conn = get_db()

    try:
        conn.execute(
            """
            INSERT INTO users
            (name, email, password, role)
            VALUES (?, ?, ?, ?)
            """,
            (
                name,
                email,
                password,
                role
            )
        )

        conn.commit()

        return {
            "message": "Account created successfully!"
        }

    except sqlite3.IntegrityError:

        raise HTTPException(
            status_code=400,
            detail="An account with this email already exists."
        )

    finally:
        conn.close()


@app.post("/api/login")
async def login(
    email: str = Form(...),
    password: str = Form(...)
):

    email = email.strip().lower()

    conn = get_db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE email = ?
        AND password = ?
        """,
        (
            email,
            password
        )
    ).fetchone()

    conn.close()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password."
        )

    user_data = {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "city": user["city"] or "",
        "bio": user["bio"] or ""
    }

    if user["role"] == "recruiter":
        redirect_url = "/recruiter/dashboard"
    else:
        redirect_url = "/candidate/dashboard"

    return {
        "message": "Login successful.",
        "user": user_data,
        "redirect_url": redirect_url
    }


# =========================================================
# CANDIDATE PROFILE
# =========================================================

@app.post("/api/candidate/profile")
async def update_candidate_profile(
    candidate_id: int = Form(...),
    name: str = Form(...),
    city: str = Form(...),
    bio: str = Form(...)
):

    name = name.strip()
    city = city.strip()
    bio = bio.strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="Name cannot be empty."
        )

    conn = get_db()

    candidate = conn.execute(
        """
        SELECT id, role
        FROM users
        WHERE id = ?
        """,
        (candidate_id,)
    ).fetchone()

    if not candidate:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Candidate not found."
        )

    if candidate["role"] != "candidate":
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Only candidates can update this profile."
        )

    conn.execute(
        """
        UPDATE users
        SET name = ?,
            city = ?,
            bio = ?
        WHERE id = ?
        """,
        (
            name,
            city,
            bio,
            candidate_id
        )
    )

    conn.commit()
    conn.close()

    return {
        "message": "Profile updated successfully."
    }


# =========================================================
# JOBS - GET
# =========================================================

@app.get("/api/jobs")
async def get_jobs(
    city: Optional[str] = None
):

    conn = get_db()

    if city and city.strip():

        jobs = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE LOWER(city) LIKE LOWER(?)
            ORDER BY id DESC
            """,
            (
                f"%{city.strip()}%",
            )
        ).fetchall()

    else:

        jobs = conn.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY id DESC
            """
        ).fetchall()

    result = [
        dict(job)
        for job in jobs
    ]

    conn.close()

    return {
        "jobs": result
    }


# =========================================================
# GET SINGLE JOB
# =========================================================

@app.get("/api/jobs/{job_id}")
async def get_single_job(job_id: int):

    conn = get_db()

    job = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE id = ?
        """,
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


# =========================================================
# CREATE JOB
# =========================================================

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

    title = title.strip()
    company = company.strip()
    city = city.strip()
    salary = salary.strip()
    job_type = job_type.strip()
    description = description.strip()

    if not all([
        title,
        company,
        city,
        salary,
        job_type,
        description
    ]):
        raise HTTPException(
            status_code=400,
            detail="All job fields are required."
        )

    conn = get_db()

    recruiter = conn.execute(
        """
        SELECT id, role
        FROM users
        WHERE id = ?
        """,
        (recruiter_id,)
    ).fetchone()

    if not recruiter:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Recruiter not found."
        )

    if recruiter["role"] != "recruiter":
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Only recruiters can post jobs."
        )

    created_at = datetime.now().isoformat()

    cursor = conn.execute(
        """
        INSERT INTO jobs
        (
            recruiter_id,
            title,
            company,
            city,
            salary,
            job_type,
            description,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            recruiter_id,
            title,
            company,
            city,
            salary,
            job_type,
            description,
            created_at
        )
    )

    conn.commit()

    job_id = cursor.lastrowid

    conn.close()

    return {
        "message": "Job posted successfully.",
        "job_id": job_id
    }


# =========================================================
# UPDATE JOB
# =========================================================

@app.put("/api/jobs/{job_id}")
async def update_job(
    job_id: int,
    recruiter_id: int = Form(...),
    title: str = Form(...),
    company: str = Form(...),
    city: str = Form(...),
    salary: str = Form(...),
    job_type: str = Form(...),
    description: str = Form(...)
):

    conn = get_db()

    job = conn.execute(
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

    if job["recruiter_id"] != recruiter_id:
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="You can only edit your own jobs."
        )

    conn.execute(
        """
        UPDATE jobs
        SET title = ?,
            company = ?,
            city = ?,
            salary = ?,
            job_type = ?,
            description = ?
        WHERE id = ?
        """,
        (
            title.strip(),
            company.strip(),
            city.strip(),
            salary.strip(),
            job_type.strip(),
            description.strip(),
            job_id
        )
    )

    conn.commit()
    conn.close()

    return {
        "message": "Job updated successfully."
    }


# =========================================================
# DELETE JOB
# =========================================================

@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: int,
    recruiter_id: int
):

    conn = get_db()

    job = conn.execute(
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

    if job["recruiter_id"] != recruiter_id:
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="You can only delete your own jobs."
        )

    conn.execute(
        """
        DELETE FROM jobs
        WHERE id = ?
        """,
        (job_id,)
    )

    conn.commit()
    conn.close()

    return {
        "message": "Job deleted successfully."
    }


# =========================================================
# APPLY FOR JOB + AI ANALYSIS
# =========================================================

@app.post("/api/apply")
async def apply_job(
    job_id: int = Form(...),
    candidate_id: int = Form(...),
    resume: UploadFile = File(...)
):

    # -----------------------------------------------------
    # VALIDATE FILE
    # -----------------------------------------------------

    if not resume.filename:
        raise HTTPException(
            status_code=400,
            detail="Please upload your CV."
        )

    if not resume.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF CV files are allowed."
        )

    # -----------------------------------------------------
    # VALIDATE CANDIDATE
    # -----------------------------------------------------

    conn = get_db()

    candidate = conn.execute(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (candidate_id,)
    ).fetchone()

    if not candidate:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Candidate not found."
        )

    if candidate["role"] != "candidate":
        conn.close()

        raise HTTPException(
            status_code=403,
            detail="Only candidates can apply for jobs."
        )

    # -----------------------------------------------------
    # GET JOB
    # -----------------------------------------------------

    job = conn.execute(
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

    # -----------------------------------------------------
    # PREVENT DUPLICATE APPLICATION
    # -----------------------------------------------------

    existing = conn.execute(
        """
        SELECT id
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
            detail="You have already applied for this job."
        )

    # -----------------------------------------------------
    # EXTRACT PDF TEXT
    # -----------------------------------------------------

    extracted_text = ""

    try:

        with pdfplumber.open(resume.file) as pdf:

            for page in pdf.pages:

                page_text = page.extract_text()

                if page_text:
                    extracted_text += (
                        page_text + "\n"
                    )

    except Exception as error:

        print("PDF extraction error:", error)

        conn.close()

        raise HTTPException(
            status_code=400,
            detail="Could not read the uploaded PDF."
        )

    extracted_text = extracted_text.strip()

    if not extracted_text:

        conn.close()

        raise HTTPException(
            status_code=400,
            detail=(
                "No readable text was found in this PDF. "
                "Please upload a text-based CV."
            )
        )

    # -----------------------------------------------------
    # AI ANALYSIS
    # -----------------------------------------------------

    ai_data = analyze_resume_against_job(
        extracted_text,
        job["description"]
    )

    # -----------------------------------------------------
    # STORE APPLICATION
    # -----------------------------------------------------

    applied_at = datetime.now().isoformat()

    conn.execute(
        """
        INSERT INTO applications
        (
            job_id,
            candidate_id,
            match_score,
            score_percentage,
            feedback,
            matching_skills,
            missing_skills,
            resume_text,
            applied_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            candidate_id,
            ai_data["match_score"],
            ai_data["score_percentage"],
            ai_data["feedback"],
            json.dumps(
                ai_data["matching_skills"]
            ),
            json.dumps(
                ai_data["missing_skills"]
            ),
            extracted_text,
            applied_at
        )
    )

    conn.commit()

    application_id = conn.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    conn.close()

    return {
        "message": "Application submitted successfully.",
        "application_id": application_id,
        "analysis": {
            "score_percentage":
                ai_data["score_percentage"],

            "match_score":
                ai_data["match_score"],

            "feedback":
                ai_data["feedback"],

            "matching_skills":
                ai_data["matching_skills"],

            "missing_skills":
                ai_data["missing_skills"]
        }
    }


# =========================================================
# CANDIDATE APPLICATION HISTORY
# =========================================================

@app.get("/api/candidate/applications/{candidate_id}")
async def get_candidate_applications(
    candidate_id: int
):

    conn = get_db()

    applications = conn.execute(
        """
        SELECT
            a.id,
            a.job_id,
            a.candidate_id,
            a.match_score,
            a.score_percentage,
            a.feedback,
            a.matching_skills,
            a.missing_skills,
            a.applied_at,

            j.title AS job_title,
            j.company AS company_name,
            j.city AS location,
            j.salary,
            j.job_type

        FROM applications a

        JOIN jobs j
        ON a.job_id = j.id

        WHERE a.candidate_id = ?

        ORDER BY a.id DESC
        """,
        (candidate_id,)
    ).fetchall()

    conn.close()

    result = []

    for row in applications:

        result.append({
            "id": row["id"],
            "job_id": row["job_id"],
            "candidate_id": row["candidate_id"],

            "job_title":
                row["job_title"],

            "company_name":
                row["company_name"],

            "location":
                row["location"],

            "salary":
                row["salary"],

            "job_type":
                row["job_type"],

            "score_percentage":
                row["score_percentage"] or 0,

            "match_score":
                row["match_score"] or "0%",

            "feedback":
                row["feedback"] or "",

            "matching_skills":
                safe_json_list(
                    row["matching_skills"]
                ),

            "missing_skills":
                safe_json_list(
                    row["missing_skills"]
                ),

            "applied_at":
                row["applied_at"]
        })

    return {
        "applications": result
    }


# =========================================================
# RECRUITER APPLICATIONS
# =========================================================

@app.get("/api/recruiter/applications/{recruiter_id}")
async def get_recruiter_applications(
    recruiter_id: int
):

    conn = get_db()

    applications = conn.execute(
        """
        SELECT

            a.id,
            a.job_id,
            a.candidate_id,

            a.match_score,
            a.score_percentage,
            a.feedback,

            a.matching_skills,
            a.missing_skills,

            a.resume_text,
            a.applied_at,

            j.title AS job_title,
            j.company AS company_name,
            j.city AS job_city,
            j.salary,
            j.job_type,

            u.name AS candidate_name,
            u.email AS candidate_email,
            u.city AS candidate_city,
            u.bio AS candidate_bio

        FROM applications a

        JOIN jobs j
        ON a.job_id = j.id

        JOIN users u
        ON a.candidate_id = u.id

        WHERE j.recruiter_id = ?

        ORDER BY a.score_percentage DESC, a.id DESC
        """,
        (recruiter_id,)
    ).fetchall()

    conn.close()

    result = []

    for row in applications:

        result.append({

            "id":
                row["id"],

            "job_id":
                row["job_id"],

            "candidate_id":
                row["candidate_id"],

            "job_title":
                row["job_title"],

            "company_name":
                row["company_name"],

            "job_city":
                row["job_city"],

            "salary":
                row["salary"],

            "job_type":
                row["job_type"],

            "candidate_name":
                row["candidate_name"],

            "candidate_email":
                row["candidate_email"],

            "candidate_city":
                row["candidate_city"] or "",

            "candidate_bio":
                row["candidate_bio"] or "",

            "score_percentage":
                row["score_percentage"] or 0,

            "match_score":
                row["match_score"] or "0%",

            "feedback":
                row["feedback"] or "",

            "matching_skills":
                safe_json_list(
                    row["matching_skills"]
                ),

            "missing_skills":
                safe_json_list(
                    row["missing_skills"]
                ),

            "resume_text":
                row["resume_text"] or "",

            "applied_at":
                row["applied_at"]
        })

    return {
        "applications": result
    }


# =========================================================
# OLD COMPATIBLE APPLICATION ROUTE
# =========================================================

@app.get("/api/applications/{job_id}")
async def get_applications(job_id: int):

    conn = get_db()

    applications = conn.execute(
        """
        SELECT

            a.*,

            j.title AS job_title,
            j.company AS company_name,
            j.city AS location,

            u.name AS candidate_name,
            u.email AS candidate_email,
            u.city AS candidate_city,
            u.bio AS candidate_description

        FROM applications a

        JOIN jobs j
        ON a.job_id = j.id

        JOIN users u
        ON a.candidate_id = u.id

        WHERE a.job_id = ?

        ORDER BY a.score_percentage DESC, a.id DESC
        """,
        (job_id,)
    ).fetchall()

    conn.close()

    result = []

    for row in applications:

        result.append({

            "id":
                row["id"],

            "job_id":
                row["job_id"],

            "job_title":
                row["job_title"],

            "company_name":
                row["company_name"],

            "location":
                row["location"],

            "candidate_id":
                row["candidate_id"],

            "candidate_name":
                row["candidate_name"],

            "candidate_email":
                row["candidate_email"],

            "candidate_city":
                row["candidate_city"] or "",

            "candidate_description":
                row["candidate_description"] or "",

            "score_percentage":
                row["score_percentage"] or 0,

            "match_score":
                row["match_score"] or "0%",

            "feedback":
                row["feedback"] or "",

            "matching_skills":
                safe_json_list(
                    row["matching_skills"]
                ),

            "missing_skills":
                safe_json_list(
                    row["missing_skills"]
                ),

            "resume_text":
                row["resume_text"] or "",

            "applied_at":
                row["applied_at"]
        })

    return {
        "applications": result
    }


# =========================================================
# HEALTH CHECK
# =========================================================

@app.get("/api/health")
async def health():

    return {
        "status": "ok",
        "application": "Career Hub",
        "database": "SQLite",
        "time": datetime.now().isoformat()
    }


# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True
    )