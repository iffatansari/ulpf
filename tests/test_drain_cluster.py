import pytest
from fastapi import HTTPException

from parsers import drain_miner
from routes.drain import DrainClusterRequest, cluster_logs


def test_cluster_lines_groups_variable_lines_into_one_template():
    lines = [
        "request from 203.0.113.9 to /login took 12ms",
        "request from 198.51.100.4 to /login took 14ms",
        "worker WARN queue depth 41 items",
    ]

    results = drain_miner.cluster_lines(lines)

    ids = [cid for cid, _ in results]
    assert len(results) == 3
    assert ids[0] == ids[1]
    assert ids[1] != ids[2]
    # The final template for the merged cluster carries the wildcards.
    assert "<*>" in results[1][1]


def test_cluster_lines_skips_blank_lines():
    results = drain_miner.cluster_lines(["", "  ", "hello"])

    assert len(results) == 1
    assert results[0][1] == "hello"


def test_cluster_lines_is_deterministic_per_call():
    lines = [
        "request from 203.0.113.9 to /login took 12ms",
        "request from 198.51.100.4 to /login took 14ms",
    ]

    first = drain_miner.cluster_lines(lines)
    second = drain_miner.cluster_lines(lines)

    assert first == second


def test_cluster_route_groups_and_filters_noise():
    content = (
        "request from 203.0.113.9 to /login took 12ms\n"
        "request from 198.51.100.4 to /login took 14ms\n"
        "==============================\n"
    )

    result = cluster_logs(DrainClusterRequest(content=content))

    clusters = result["clusters"]
    assert len(clusters) == 1
    assert clusters[0]["count"] == 2
    assert "<*>" in clusters[0]["template"]
    assert len(clusters[0]["examples"]) == 2


def test_cluster_route_returns_empty_for_no_usable_lines():
    result = cluster_logs(DrainClusterRequest(content="\n====\n12345\n"))
    assert result["clusters"] == []


def test_cluster_route_rejects_oversized_streams():
    with pytest.raises(HTTPException) as exc:
        cluster_logs(DrainClusterRequest(content="a\n" * 20_001))
    assert exc.value.status_code == 413