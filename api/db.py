import os
from opensearchpy import OpenSearch


_OPENSEARCH_CLIENT = None


def get_opensearch_client() -> OpenSearch:
    global _OPENSEARCH_CLIENT
    if _OPENSEARCH_CLIENT is None:
        url = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
        _OPENSEARCH_CLIENT = OpenSearch(
            hosts=[url],
            timeout=10,
        )
    return _OPENSEARCH_CLIENT