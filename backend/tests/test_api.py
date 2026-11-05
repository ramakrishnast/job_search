from app.db import SessionLocal
from app.models import Company, Job

GOOD_KIT = {
    "cover_letter": " ".join(["I build production agent systems."] * 30),
    "why_company": ["Mission fit", "Agents focus", "(verify) Strong eng culture"],
    "interview_questions": [
        {"question": f"Q{i}", "why_they_ask": "w", "talking_points": ["supervisor-subagent system", "A2A CRM writes"]} for i in range(5)
    ],
    "outreach": {
        "linkedin_note": "Hi! I build production multi-agent systems.",
        "email_subject": "Applied AI role",
        "email_body": "Hello, short note.",
    },
}


def seed_job(**kw):
    db = SessionLocal()
    c = Company(name="Acme AI", ats="greenhouse", slug="acme")
    db.add(c)
    db.flush()
    j = Job(
        company_id=c.id,
        source="greenhouse",
        external_id="1",
        title="Senior Applied AI Engineer",
        location="San Francisco, CA",
        locations=["San Francisco, CA"],
        description_text="Build LLM agents with Python. 5+ years.",
        content_hash="abc",
        matches_filter=True,
        apply_url="https://example.com/apply",
        **kw,
    )
    db.add(j)
    db.commit()
    jid = j.id
    db.close()
    return jid


def test_health_and_status(client):
    assert client.get("/api/health").json()["status"] == "ok"
    st = client.get("/api/status").json()
    assert st["llm"]["configured"] is False and st["counts"]["questions"] >= 40


def test_pipeline_lifecycle_and_dashboard(client):
    jid = seed_job()
    r = client.post(f"/api/jobs/{jid}/application", params={"stage": "shortlisted"})
    assert r.status_code == 201
    app_id = r.json()["id"]
    # opening the apply link never submits anything; it only records intent
    r = client.post(f"/api/jobs/{jid}/apply-opened").json()
    assert r["apply_url"] == "https://example.com/apply"
    for stage in ("applied", "recruiter_screen", "interviewing"):
        assert client.patch(f"/api/applications/{app_id}", json={"stage": stage}).status_code == 200
    r = client.patch(
        f"/api/applications/{app_id}", json={"notes": "Great call", "next_action": "Send thank-you", "follow_up_at": "2030-01-01T10:00:00Z"}
    )
    body = r.json()
    assert body["applied_at"] is not None and [e["to"] for e in body["events"]][-1] == "interviewing"
    assert client.patch(f"/api/applications/{app_id}", json={"stage": "nope"}).status_code == 400
    c = client.post(f"/api/applications/{app_id}/contacts", json={"name": "Jane Recruiter", "role": "Recruiter"})
    assert c.status_code == 201

    board = client.get("/api/pipeline").json()
    assert [x["id"] for x in board["columns"]["interviewing"]] == [jid]
    d = client.get("/api/dashboard").json()["pipeline"]
    assert d["applied_total"] == 1 and d["response_rate"] == 1.0
    conv = {(x["from"], x["to"]): x["rate"] for x in d["conversion"]}
    assert conv[("applied", "recruiter_screen")] == 1.0
    assert sum(w["applications"] for w in d["applications_per_week"]) == 1


def test_jobs_list_filters_and_heuristic_score(client):
    jid = seed_job()
    r = client.post(f"/api/jobs/{jid}/score").json()
    assert r["fit"]["method"] == "heuristic"
    jobs = client.get("/api/jobs", params={"q": "acme"}).json()
    assert jobs["total"] == 1 and jobs["jobs"][0]["fit"]["score"] == r["fit"]["score"]
    assert client.get("/api/jobs", params={"q": "zzz"}).json()["total"] == 0


def test_llm_features_need_key(client):
    jid = seed_job()
    r = client.post(f"/api/jobs/{jid}/tailor")
    assert r.status_code == 503 and r.json()["code"] == "llm_not_configured"
    r = client.post("/api/interviews", json={"kind": "system_design"})
    assert r.status_code == 201  # starting works (first question is from the bank)
    sid = r.json()["id"]
    r = client.post(f"/api/interviews/{sid}/answer", json={"text": "my answer"})
    assert r.status_code == 503
    # the answer was rolled back, so it can be resubmitted
    turns = client.get(f"/api/interviews/{sid}").json()["turns"]
    assert [t["role"] for t in turns] == ["interviewer"]


def test_tailoring_and_exports(client, fake_llm):
    from .test_scoring import PARSE

    tailored = {
        "summary": "Applied AI engineer.",
        "experience": [
            {
                "experience_id": "e1",
                "bullets": [
                    {
                        "text": "Architected a supervisor-subagent multi-agent orchestration system shipped to production",
                        "source_bullet_ids": ["e1b4"],
                    }
                ],
            }
        ],
        "skills": [],
        "keywords_covered": ["agents"],
        "notes": "n",
    }
    p = fake_llm({"jd_parse": [PARSE], "tailor_resume": [tailored], "application_kit": [GOOD_KIT]})
    jid = seed_job()
    r = client.post(f"/api/jobs/{jid}/tailor")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"resume", "kit"}
    client.post(f"/api/jobs/{jid}/tailor")  # cached → no new calls
    assert p.count("tailor_resume") == 1 and p.count("application_kit") == 1
    detail = client.get(f"/api/jobs/{jid}").json()
    assert detail["tailoring"]["kit"]["data"]["outreach"]["email_subject"] == "Applied AI role"

    d = client.get(f"/api/jobs/{jid}/export/resume.docx")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    pdf = client.get(f"/api/jobs/{jid}/export/resume.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    cl = client.get(f"/api/jobs/{jid}/export/cover-letter.docx")
    assert cl.status_code == 200 and cl.content[:2] == b"PK"
    import io

    from docx import Document

    text = "\n".join(p.text for p in Document(io.BytesIO(d.content)).paragraphs)
    assert "supervisor-subagent" in text


def test_profile_resume_heuristic_structuring(client):
    resume = """Jane Doe
SUMMARY
Senior AI engineer building production LLM systems for enterprise users.
EXPERIENCE
Senior AI Engineer | Acme Corp | Dec 2021 – Present
• Built a multi-agent orchestration system used by 300 sellers
• Owned the agent action service end to end
Data Engineer | Beta Inc | 2015 – 2021
- Built Spark pipelines processing 2TB daily
EDUCATION
M.S. Data Science, University of North Texas, 2020
SKILLS
Languages: Python, SQL
"""
    r = client.post("/api/profile/resume", json={"resume_text": resume}).json()
    sr = r["structured_resume"]
    assert r["structured_method"] == "heuristic"
    assert len(sr["experience"]) == 2 and sr["experience"][0]["bullets"][0]["text"].startswith("Built a multi-agent")
    assert sr["skills"][0]["items"] == ["Python", "SQL"]
    assert client.get("/api/profile/export/resume.pdf").content[:4] == b"%PDF"


def test_manual_job_and_stories(client):
    r = client.post(
        "/api/jobs",
        json={
            "company": "Google",
            "title": "Senior AI Engineer, Gemini Apps",
            "location": "Mountain View, CA",
            "url": "https://careers.google.com/x",
            "description": "Build Gemini-powered agents. 8+ years experience.",
        },
    )
    assert r.status_code == 201 and r.json()["application"]["stage"] == "shortlisted"
    s = client.post(
        "/api/stories",
        json={
            "title": "A2A service",
            "project": {"context": "CRM writes", "role": "sole engineer"},
            "situation": "S",
            "task": "T",
            "action": "A",
            "result": "R",
            "question_ids": ["beh-sole-owner"],
        },
    )
    assert s.status_code == 201
    data = client.get("/api/stories").json()
    assert data["coverage"]["beh-sole-owner"] == [s.json()["id"]]


def test_code_run_endpoint(client):
    qs = client.get("/api/questions", params={"category": "coding"}).json()["questions"]
    q = next(x for x in qs if x["key"] == "code-cosine-topk")
    r = client.post("/api/code/run", json={"question_id": q["id"], "code": q["starter_code"]}).json()
    assert r["ok"] is False and r["total"] >= 3
