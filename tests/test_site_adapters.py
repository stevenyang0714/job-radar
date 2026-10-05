import html
import json
from datetime import date

import httpx
import pytest

from jobradar.adapters.base import find_job_posting
from jobradar.adapters.fileai import parse_cards
from jobradar.adapters.japandev import extract_jobs
from jobradar.adapters.michaelpage import parse_tiles
from jobradar.adapters.ycombinator import YCombinatorAdapter, relative_date
from jobradar.filters import expand_locations, find_term, screen_detail, screen_listing
from jobradar.models import Job, Source


def _inertia_page(props):
    return f'<div id="app" data-page="{html.escape(json.dumps({"props": props}))}"></div>'


def test_yc_lists_jobs_and_fetches_markdown_description():
    posting = {
        "id": 1, "title": "Founding BD Lead", "url": "/companies/acme/jobs/1-bd", "location": "Singapore / Remote (Asia)",
        "type": "Full-time", "salaryRange": "$90K - $140K", "createdAt": "3 days", "companyName": "Acme",
        "companyBatchName": "S25", "companyOneLiner": "AI for logistics", "visa": "Will sponsor", "minExperience": "3+ years",
        "prettyRole": "Sales", "roleSpecificType": "",
    }

    def handler(request):
        if request.url.path == "/jobs/location/singapore":
            return httpx.Response(200, text=_inertia_page({"jobPostings": [posting]}))
        assert request.url.path == "/companies/acme/jobs/1-bd"
        return httpx.Response(200, text=_inertia_page({"job": {**posting, "description": "## Role\nGrow partnerships"}}))

    adapter = YCombinatorAdapter(httpx.Client(transport=httpx.MockTransport(handler)), delay=0)
    source = Source(name="yc", company="Y Combinator", url="https://www.ycombinator.com/jobs/location/singapore")

    [job] = adapter.list_jobs(source)
    assert (job.company, job.salary, job.description) == ("Acme", "$90K - $140K（年薪）", None)
    detailed = adapter.fetch_detail(job)
    assert "簽證：Will sponsor" in detailed.description and "Grow partnerships" in detailed.description


@pytest.mark.parametrize(
    "text, expected",
    [("3 days", date(2026, 10, 2)), ("2 weeks", date(2026, 9, 21)), ("about 1 month", date(2026, 9, 5)), ("5 hours", date(2026, 10, 5)), (None, None)],
)
def test_relative_date(text, expected):
    assert relative_date(text, today=date(2026, 10, 5)) == expected


def test_fileai_cards_parsed_and_unescaped():
    card = (
        '<div fs-list-element="item" role="listitem" class="career-item w-dyn-item"><div class="career-heading">Customer &amp; Ops Lead</div>'
        '<div class="career-text-grey">CX &amp; Operations</div><div fs-list-field="type">Full-time</div>'
        '<div fs-list-field="arrange">Hybrid</div><div fs-list-field="make">Japan</div>'
        '<a href="https://drive.google.com/file/d/ABC123/view?usp=sharing" class="x"><div>Job Description</div></a>'
        '<a href="https://forms.gle/xyz" class="y"><div>Apply</div></a></div>'
    )
    empty_link = card.replace("https://drive.google.com/file/d/ABC123/view?usp=sharing", "#").replace("Customer &amp; Ops Lead", "Delivery Lead")
    first, second = parse_cards(card + empty_link + '<div class="w-pagination-wrapper"></div>')
    assert first["title"] == "Customer & Ops Lead" and first["department"] == "CX & Operations"
    assert (first["location"], first["arrangement"]) == ("Japan", "Hybrid")
    assert first["description_url"].endswith("/file/d/ABC123/view?usp=sharing")
    assert second["title"] == "Delivery Lead" and second["description_url"] is None


def test_nuxt_payload_is_unflattened_and_wrapped_duplicates_skipped():
    # 索引 1 是職缺；索引 9 是同一職缺的「包裝」版本（欄位指向 {"value": …}）
    data = [
        {"jobs": 8},
        {"id": 2, "title": 3, "slug": 4, "japanese_level_enum": 5, "company": 6},
        "42", "Partnerships Lead", "acme-partnerships", "japanese_level_not_required",
        {"name": 3, "slug": 7}, "acme",
        ["Reactive", 1],
        {"id": 10, "title": 10, "slug": 10, "japanese_level_enum": 10},
        {"value": 2},
    ]
    page = f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(data)}</script>'
    [job] = extract_jobs(page)
    assert (job["id"], job["title"], job["company"]["slug"]) == ("42", "Partnerships Lead", "acme")


def test_michaelpage_tiles_parsed():
    tile = (
        '<li class="views-row"><div about="/en/job-detail/risk-lead/ref/jn-102026-1" class="job-tile search-job-tile">'
        '<div class="job-title " id="1"><h3><a href="#">Risk Lead</a></h3></div>'
        '<div class="job-location"><i></i> Tokyo</div><div class="job-contract-type"><i></i> Permanent</div>'
        '<div class="job-salary"><i></i> ¥12,000,000 per year</div><div class="job-company-type"><i></i> Foreign Multinational</div></li>'
    )
    [parsed] = parse_tiles(tile)
    assert (parsed["job_id"], parsed["title"], parsed["location"], parsed["salary"]) == ("jn-102026-1", "Risk Lead", "Tokyo", "¥12,000,000 per year")


def test_job_posting_ld_tolerates_raw_newlines():
    page = '<script type="application/ld+json">{"@type": "JobPosting", "description": "line one\nline two", "datePosted": "2026-10-01"}</script>'
    assert find_job_posting(page)["datePosted"] == "2026-10-01"


def test_japanese_text_rule():
    source = Source(name="mp", company="Michael Page", url="https://www.michaelpage.co.jp/en/jobs")
    job = Job(source="mp", ats="michaelpage", company="MP", job_id="1", title="Risk Lead", url="https://x")
    assert screen_listing(job.model_copy(update={"title": "内部統制マネージャー"}), source, check_location=False).reason == "日文職缺"
    assert screen_detail(job.model_copy(update={"description": "クレジットリスク、不正対策、回収領域における内部統制・ガバナンスフレームワーク"}), source).reason == "日文職缺"
    assert screen_detail(job.model_copy(update={"description": "負責風險治理與內部控制，需具備金融業經驗"}), source).passed  # 中文不受影響


def test_australia_and_japan_aliases():
    terms = expand_locations(["Australia", "Japan"])
    assert find_term(terms, "Melbourne, VIC") and find_term(terms, "東京都") and find_term(terms, "Tokyo / Singapore")
    assert not find_term(terms, "Austria")
