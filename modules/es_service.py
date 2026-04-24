import os
from elasticsearch import Elasticsearch

class ESClient:
    def __init__(self):
        self.client = None
        self.connect()

    def connect(self):
        host = os.getenv('ELASTICSEARCH_HOST', 'localhost')
        port = int(os.getenv('ELASTICSEARCH_PORT', 9200))
        scheme = os.getenv('ELASTICSEARCH_SCHEME', 'http')
        # Add auth if needed: basic_auth=(user, pass)
        
        self.client = Elasticsearch(
            [{'host': host, 'port': port, 'scheme': scheme}]
        )

    def get_client(self):
        return self.client

# Singleton instance
es_service = ESClient()
