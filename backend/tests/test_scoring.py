from app.models import Company, Job
from app.services.profile import get_profile
from app.services.scoring import heuristic_score, score_job

from .test_llm_schemas import GOOD_FIT

PARSE = {
    "title_normalized": "Applied AI Engineer",
    "seniority": "senior",
    "required_skills": ["Python", "LLM agents"],
    "nice_to_haves": ["evals"],
    "years_experience_min": 5,
    "location": "SF",
    "remote_policy": "hybrid",
    "comp_min": 300000,
    "comp_max": 405000,
    "comp_currency": "USD",
    "comp_raw": "$300,000 - $405,000",
    "responsibilities": ["build agents"],
    "role_summary": "Build agents.",
}


def make_job(db, text="Build LLM agents in Python. 5+ years. Evals experience preferred.", h="h1"):
    c = Company(name="Acme", ats="greenhouse", slug="acme")
    db.add(c)
    db.flush()
    j = Job(
        company_id=c.id,
        source="greenhouse",
        external_id="1",
        title="Senior Applied AI Engineer",
        location="San Francisco",
        description_text=text,
        content_hash=h,
    )
    db.add(j)
    db.commit()
    return j


def test_score_once_then_cached(fake_llm, db):
    p = fake_llm({"jd_parse": [PARSE], "fit_score": [GOOD_FIT]})
    from app.llm.client import get_llm

    job = make_job(db)
    prof = get_profile(db)
    f1 = score_job(db, get_llm(), job, prof)
    f2 = score_job(db, get_llm(), job, prof)
    assert f1.score == f2.score == 82 and f1.method == "llm"
    assert p.count("jd_parse") == 1 and p.count("fit_score") == 1
    assert f1.data["parsed"]["comp_max"] == 405000
    # prompt carries the candidate background in the cached system block
    req = next(c for c in p.calls if c.task == "fit_score")
    from app.config import get_settings

    assert "Workday" in req.cached_context and req.model == get_settings().llm_model_default
    assert next(c for c in p.calls if c.task == "jd_parse").model == get_settings().llm_model_parse


def test_force_rescore_and_jd_change(fake_llm, db):
    p = fake_llm({"jd_parse": [PARSE], "fit_score": [GOOD_FIT]})
    from app.llm.client import get_llm

    job = make_job(db)
    prof = get_profile(db)
    score_job(db, get_llm(), job, prof)
    score_job(db, get_llm(), job, prof, force=True)
    assert p.count("fit_score") == 2 and p.count("jd_parse") == 1
    job.content_hash, job.description_text = "h2", job.description_text + " Now also Rust."
    db.commit()
    score_job(db, get_llm(), job, prof)
    assert p.count("jd_parse") == 2 and p.count("fit_score") == 3


def test_heuristic_without_key(db):
    from app.llm.client import get_llm

    job = make_job(db)
    f = score_job(db, get_llm(), job, get_profile(db))
    assert f.method == "heuristic" and 0 <= f.score <= 100
    assert len(f.data["strengths"]) == 3 and len(f.data["gaps"]) == 3
    assert "Heuristic" in f.data["verdict_line"]


def test_heuristic_reports_gaps(db):
    job = make_job(db, text="Requires Rust, Kubernetes, fine-tuning with LoRA, and PyTorch.")
    h = heuristic_score(job, get_profile(db))
    assert any("Fine-tuning" in g or "PyTorch" in g for g in h["gaps"])


def test_heuristic_is_not_overconfident_on_thin_jds(db):
    thin = make_job(db, text="Work with customers in production.")
    h = heuristic_score(thin, get_profile(db))
    assert h["score"] < 75  # 2/2 keyword areas is weak evidence, not a "Strong" fit
