import pytest

from app.services.filtering import classify_seniority, evaluate, load_filter_config


@pytest.fixture(scope="module")
def cfg():
    return load_filter_config()


@pytest.mark.parametrize(
    "title,locs,remote,expected",
    [
        ("Software Engineer, Applied AI", ["San Francisco, CA"], False, True),
        ("Senior Machine Learning Engineer", ["New York, NY"], False, True),
        ("Staff AI Engineer, Agents", ["Remote - US"], True, True),
        ("Forward Deployed Engineer", ["Atlanta, GA"], False, True),
        ("Senior LLM Engineer", ["Remote"], True, True),
        ("LLM Researcher", ["San Francisco, CA"], False, False),
        ("Senior ML Engineer", ["Remote - UK"], True, False),
        ("Senior ML Engineer", ["London, UK"], False, False),
        ("Research Scientist, LLM", ["San Francisco, CA"], False, False),
        ("Junior ML Engineer", ["San Francisco, CA"], False, False),
        ("Machine Learning Engineer I", ["San Francisco, CA"], False, False),
        ("Engineering Manager, Applied AI", ["San Francisco, CA"], False, False),
        ("Senior Backend Engineer", ["San Francisco, CA"], False, False),
        ("Recruiter, AI", ["San Francisco, CA"], False, False),
        ("Senior Applied AI Engineer", ["Toronto, Canada", "Remote (US)"], True, True),
        ("AI Engineer", [], False, False),
    ],
)
def test_evaluate(cfg, title, locs, remote, expected):
    assert evaluate(title, locs, remote, cfg).passed is expected


def test_location_tags(cfg):
    r = evaluate("Senior AI Engineer", ["San Francisco, CA | New York City, NY"], False, cfg)
    assert set(r.location_tags) == {"bay_area", "nyc"}
    r2 = evaluate("Senior AI Engineer", ["Remote - US"], True, cfg)
    assert r2.location_tags == ["remote_us"]


def test_reasons_explain_rejection(cfg):
    r = evaluate("Account Executive, AI", ["Paris"], False, cfg)
    assert not r.passed
    assert any("excluded" in x for x in r.reasons)
    assert any("location" in x for x in r.reasons)


def test_word_boundaries(cfg):
    # "agent" must not match inside "management"; "ai" family terms need boundaries
    assert not evaluate("Senior Product Management Lead", ["San Francisco"], False, cfg).title_ok
    assert evaluate("Senior Agentic Systems Engineer", ["San Francisco"], False, cfg).title_ok


@pytest.mark.parametrize(
    "title,level",
    [
        ("Senior AI Engineer", "senior"),
        ("Staff ML Engineer", "staff"),
        ("Principal Engineer, LLM", "principal"),
        ("Member of Technical Staff, Applied AI", "senior"),
        ("Software Engineer, Applied AI", "unleveled"),
        ("ML Engineer II", "mid"),
        ("ML Engineer III", "senior"),
        ("Associate ML Engineer", "junior"),
    ],
)
def test_seniority(cfg, title, level):
    assert classify_seniority(title, cfg) == level


@pytest.mark.parametrize(
    "title,expected",
    [  # real titles seen in live discovery
        ("AI deployment engineer (West)", True),
        ("Senior Software Engineer, AI Native Web Platform", True),
        ("Training: ML Framework Engineer", True),
        ("Deployed Architect, Professional Services (NYC)", True),
        ("Sr. Forward Deployed Engineer (FDE) - Retail", True),
        ("Applied AI Engineer, Sign in with ChatGPT", True),
        ("Member of Technical Staff (Software Engineer, Applied AI)", True),
        ("Product Manager, Agent Harness & Modelling", False),
        ("Technical Program Manager, Safeguards (Infrastructure & Evals)", False),
        ("Member of Technical Staff (TPM, Inference)", False),
        ("Strategist, Agent Development - Public Sector", False),
        ("Forward Deployed Strategist", False),
        ("Agent Development Manager", False),
        ("Manager, Forward Deployed Engineering- NYC", False),
        ("Applied AI Delivery Lead", False),
        ("Research Engineer/Research Scientist, RL/Reasoning", False),
        ("Mechanical Engineer, Hardware Systems", False),
        ("Staff Software Engineer - Backend", False),
        ("Technical Sourcer, AI", False),
        ("Transformative AI Research Economist, Economic Research", False),
        ("AI Scientist", False),
        ("Staff Software Engineer, AI Runtime", True),
        ("Software Engineer, CI/CD & ML Infrastructure", True),
        ("Research Engineer / Scientist, Data & Evals", False),
        ("Quality Lead, Gen AI", False),
        ("Security Risk & Compliance, Agent Security", False),
        ("Applied AI Architect, Commercial", True),
    ],
)
def test_real_world_titles(cfg, title, expected):
    assert evaluate(title, ["San Francisco, CA"], False, cfg).title_ok is expected
