def generate_ai_resume_summary(skills: str, experience: str) -> str:
    """
    Mock AI function to generate resume summary.
    Replace with actual OpenAI/Gemini API calls when API Key is provided.
    """
    if not skills or not experience:
        return "Please provide valid skills and experience."

    return (
        f"Professional Summary: Highly motivated professional skilled in {skills}. "
        f"Proven experience in: {experience}. Demonstrates strong problem-solving abilities "
        f"and dedication to continuous growth."
    )

def optimize_resume_for_job(resume_text: str, job_description: str) -> str:
    """
    Analyzes matching keywords between resume and job description.
    """
    job_words = set(job_description.lower().split())
    resume_words = set(resume_text.lower().split())
    matched = job_words.intersection(resume_words)
    
    score = min(100, int((len(matched) / max(len(job_words), 1)) * 100 * 2.5))
    return f"Match Score: {score}%. Recommended keywords to add: {', '.join(list(job_words - resume_words)[:5])}"