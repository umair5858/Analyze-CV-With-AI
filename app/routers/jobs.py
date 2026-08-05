import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.database import get_db
from app.security import get_current_user
from app.services import calculate_ats_score, extract_pdf_text

router = APIRouter(prefix="/api", tags=["Jobs & Applications"])


class JobCreateRequest(BaseModel):
    title: str
    company: str
    location: str = ""
    skills: str  # comma-separated
    description: str


class ApplyRequest(BaseModel):
    job_id: int


# --- Jobs ---

@router.get("/jobs")
def get_all_jobs(db: sqlite3.Connection = Depends(get_db)):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM jobs ORDER BY id DESC")
    return [dict(row) for row in cursor.fetchall()]


@router.post("/jobs")
def post_job(
    data: JobCreateRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if user["role"] != "recruiter":
        raise HTTPException(status_code=403, detail="Sirf recruiters job post kar sakte hain.")

    cursor = db.cursor()
    cursor.execute(
        "INSERT INTO jobs (recruiter_id, title, company, location, skills, description) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            user["user_id"],
            data.title.strip(),
            data.company.strip(),
            data.location.strip(),
            data.skills.strip(),
            data.description.strip(),
        ),
    )
    db.commit()
    return {"status": "success", "message": "Job publish ho gayi!"}


@router.get("/jobs/mine")
def get_my_jobs(user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    if user["role"] != "recruiter":
        raise HTTPException(status_code=403, detail="Sirf recruiters ke liye available hai.")

    cursor = db.cursor()
    cursor.execute("SELECT * FROM jobs WHERE recruiter_id = ? ORDER BY id DESC", (user["user_id"],))
    return [dict(row) for row in cursor.fetchall()]


# --- Applications ---

@router.post("/apply")
def direct_apply(
    data: ApplyRequest,
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Sirf candidates apply kar sakte hain.")

    cursor = db.cursor()
    cursor.execute("SELECT id FROM jobs WHERE id = ?", (data.job_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Job nahi mili.")

    cursor.execute(
        "INSERT INTO applications (job_id, candidate_id, ats_score, apply_type) VALUES (?, ?, ?, ?)",
        (data.job_id, user["user_id"], 0, "Direct"),
    )
    db.commit()
    return {"status": "success", "message": "Application submit ho gayi!"}


@router.post("/apply/ai-analyze")
async def ai_analyze_and_apply(
    job_id: int = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if user["role"] != "candidate":
        raise HTTPException(status_code=403, detail="Sirf candidates ye feature use kar sakte hain.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Sirf PDF files accept hoti hain.")

    cursor = db.cursor()
    cursor.execute("SELECT skills FROM jobs WHERE id = ?", (job_id,))
    job = cursor.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Job nahi mili.")

    cv_text = await extract_pdf_text(file)
    result = calculate_ats_score(cv_text, job["skills"])

    cursor.execute(
        "INSERT INTO applications "
        "(job_id, candidate_id, ats_score, matched_skills, missing_skills, feedback, apply_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            user["user_id"],
            result["score"],
            ",".join(result["matched_skills"]),
            ",".join(result["missing_skills"]),
            result["summary"],
            "AI Analyzed",
        ),
    )
    db.commit()

    return {"status": "success", **result}


@router.get("/applications")
def get_applications_for_my_jobs(
    user: dict = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if user["role"] != "recruiter":
        raise HTTPException(status_code=403, detail="Sirf recruiters dekh sakte hain.")

    cursor = db.cursor()
    cursor.execute(
        """
        SELECT a.*, u.email AS candidate_email, j.title AS job_title
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        JOIN users u ON a.candidate_id = u.id
        WHERE j.recruiter_id = ?
        ORDER BY a.id DESC
        """,
        (user["user_id"],),
    )
    return [dict(row) for row in cursor.fetchall()]