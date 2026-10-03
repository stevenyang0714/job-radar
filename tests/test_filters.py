from jobradar.filters import Verdict, find_term, screen_detail, screen_listing
from jobradar.models import Job, Source


def _job(title, location="Singapore", description=None):
    return Job(
        source="acme",
        ats="workday",
        company="Acme",
        job_id="1",
        title=title,
        location=location,
        url="https://example.com/1",
        description=description,
    )


def _source(**kwargs):
    return Source(name="acme", company="Acme", url="https://jobs.lever.co/acme", **kwargs)


def test_english_terms_match_whole_words_only():
    assert find_term(["AI"], "Lead AI governance")
    assert not find_term(["AI"], "Maintain systems")
    assert find_term(["part-time"], "Part-Time Analyst")
    assert find_term(["Hong Kong"], "Hong Kong SAR")


def test_chinese_terms_match_substrings():
    assert find_term(["治理"], "AI治理經理")


def test_location_rejected_when_not_listed():
    verdict = screen_listing(_job("Risk Manager", location="Germany"), _source(locations=["Taiwan", "Singapore"]))
    assert verdict.status == "rejected_location"


def test_location_aliases_cover_cities_and_chinese_names():
    source = _source(locations=["Taiwan", "Hong Kong"])
    assert screen_listing(_job("Risk Manager", location="Taipei, Taiwan"), source) is None
    assert screen_listing(_job("Risk Manager", location="Hsinchu"), source) is None
    assert screen_listing(_job("Risk Manager", location="新竹市"), source) is None
    assert screen_listing(_job("Risk Manager", location="Kowloon"), source) is None
    assert screen_listing(_job("Risk Manager", location="Tokyo"), source).status == "rejected_location"


def test_location_check_skipped_when_adapter_handles_it():
    job = _job("Risk Manager", location="2 Locations")
    assert screen_listing(job, _source(locations=["Taiwan"]), check_location=False) is None


def test_exclude_checks_title_only():
    source = _source(exclude=["intern"])
    assert screen_listing(_job("Summer Intern"), source).status == "rejected_exclude"
    assert screen_listing(_job("Risk Manager", description="Mentor our intern program"), source) is None


def test_keywords_match_title_or_description():
    source = _source(keywords=["governance"])
    assert screen_detail(_job("AI Governance Lead"), source).reason == "governance"
    assert screen_detail(_job("Manager", description="Own data governance"), source).passed
    assert screen_detail(_job("Warehouse Manager", description="Run the warehouse"), source).status == "rejected_keyword"


def test_description_exclusion_runs_before_keywords():
    source = _source(keywords=["governance"], exclude_description=["Singaporeans only"])
    job = _job("AI Governance Lead", description="Open to Singaporeans only.")
    assert screen_detail(job, source) == Verdict("rejected_description", "Singaporeans only")


def test_no_keywords_means_everything_passes():
    assert screen_detail(_job("Anything"), _source()).passed
