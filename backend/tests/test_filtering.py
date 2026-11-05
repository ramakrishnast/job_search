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
