import json
from unittest.mock import Mock

import pytest

from keyword_searcher import cli


@pytest.fixture
def search(tmp_path, monkeypatch):
    transport = Mock()
    batches = []
    datasets = {}
    evidence = {}

    def start(queries, pages, country, language, cost_limit):
        batches.append(list(queries))
        run_id = f"run{len(batches)}"
        dataset_id = f"dataset{len(batches)}"
        datasets[dataset_id] = [
            {"searchQuery": {"term": query, "page": 1}, "organicResults": []} for query in queries
        ]
        evidence[run_id] = {
            "run_id": run_id,
            "dataset_id": dataset_id,
            "status": "SUCCEEDED",
            "exit_code": 0,
            "cost_usd": 0.01,
            "cost_limit_usd": cost_limit,
            "pending_requests": 0,
            "handled_requests": len(queries),
        }
        return run_id

    transport.start.side_effect = start
    transport.completed.side_effect = evidence.__getitem__
    transport.items.side_effect = datasets.__getitem__
    monkeypatch.setattr(cli, "ApifyTransport", lambda _token: transport)
    network = Mock(side_effect=AssertionError("No live requests in budget tests"))
    monkeypatch.setattr("keyword_searcher.http.HttpClient.get", network)
    query_file = tmp_path / "queries.txt"
    output = tmp_path / "output"
    args = [
        "--queries",
        str(query_file),
        "--run-dir",
        str(tmp_path / "run"),
        "--output-dir",
        str(output),
        "--no-progress",
    ]
    yield query_file, output, args, transport, batches
    network.assert_not_called()


@pytest.mark.parametrize("count,depth", [(26, 10), (91, 10), (2, None)])
def test_automatic_budget_covers_unique_queries_across_batches(search, count, depth):
    query_file, output, args, transport, batches = search
    queries = [f"query {index}" for index in range(count)]
    # BOM, blanks, whitespace and repeated queries must not inflate the budget.
    query_file.write_text("\ufeff\n" + "\n".join(queries) + "\n  query 0  \n\n", encoding="utf-8")
    if depth is not None:
        args += ["--max-pages", str(depth)]
    assert cli.main(args) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["apify_reserved_pages"] == count * (depth or 3)
    assert report["query_status_counts"] == {"complete": count}
    assert [query for batch in batches for query in batch] == queries
    assert all(len(batch) <= 20 for batch in batches)
    assert all(call.args[-1] == 1.0 for call in transport.start.call_args_list)
    before = transport.start.call_count
    assert cli.main(args) == 0
    assert transport.start.call_count == before


def test_explicit_budget_then_automatic_resume_preserves_reservations(search):
    query_file, output, args, transport, batches = search
    queries = [f"query {index}" for index in range(26)]
    query_file.write_text("\n".join(queries), encoding="utf-8")
    args += ["--max-pages", "10", "--apify-run-cost-limit-usd", "7"]
    assert cli.main(args + ["--max-apify-pages", "100"]) == 2
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["apify_reserved_pages"] == 100
    assert report["query_status_counts"] == {"complete": 10, "not_started": 16}
    assert batches == [queries[:10]]
    assert cli.main(args) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["apify_reserved_pages"] == 260
    assert report["query_status_counts"] == {"complete": 26}
    assert batches == [queries[:10], queries[10:]]
    assert all(call.args[-1] == 7.0 for call in transport.start.call_args_list)


def test_explicit_budget_below_one_query_starts_no_actor(search):
    query_file, output, args, transport, _batches = search
    query_file.write_text("query", encoding="utf-8")
    assert cli.main(args + ["--max-pages", "10", "--max-apify-pages", "9"]) == 2
    transport.start.assert_not_called()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["apify_reserved_pages"] == 0
    assert report["query_status_counts"] == {"not_started": 1}


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_explicit_budget_must_be_positive(search, limit):
    _query_file, output, args, transport, _batches = search
    with pytest.raises(SystemExit) as exc:
        cli.main(args + ["--max-apify-pages", limit])
    assert exc.value.code == 2
    transport.start.assert_not_called()
    assert not output.exists()
