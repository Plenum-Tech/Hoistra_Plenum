"""What the Documents panel offers as openable, this route must be able to serve.

building_tree._openable computes `has_file` per row, and its docstring says it "deliberately
mirrors GET /api/documents/{id}/download ... A flag that answers a different question than
the endpoint would put a live-looking control on a row that 404s". It did answer a different
question. The flag reads three places:

    plenum_cafm.documents.blob_url   OR   ingestion_documents.blob_url   OR   document_chunks

and this route read only the last two. So a document whose file is recorded in
plenum_cafm.documents — which is where the graph writes, and where the test portfolio keeps
its files — rendered an active link, and the link answered "Document not found".

Serving it is the right way round: a blob_url is a real file, and the panel was right that
there is something to open. The route's own docstring already promises "a working link even
when the original binary was never stored".
"""
from __future__ import annotations

import inspect

from src.api.routes import documents as route


SOURCE = inspect.getsource(route.download_document)


class TestTheRouteReadsEveryPlaceTheFlagReads:
    def test_it_looks_in_the_graph_documents_table(self):
        assert "plenum_cafm.documents" in SOURCE, (
            "has_file is true when plenum_cafm.documents.blob_url is set; a route that "
            "never reads that table answers 404 on a link the panel showed as live"
        )

    def test_it_still_prefers_the_ingested_original(self):
        # The ingestion row is the richer record — it carries the original filename the
        # uploader used — so it stays the first thing asked.
        i = SOURCE.index("plenum_cafm.ingestion_documents")
        d = SOURCE.index("plenum_cafm.documents")
        assert i < d

    def test_extracted_text_remains_the_fallback(self):
        assert "document_chunks" in SOURCE


class TestAMissingIngestionRowIsNoLongerTheEndOfIt:
    def test_not_found_is_not_raised_on_the_ingestion_lookup_alone(self):
        """The old route raised 404 the moment ingestion_documents had no row, before
        anything else was tried. That row is absent for every document the graph wrote."""
        before = SOURCE.split("plenum_cafm.documents")[0]
        assert "Document not found" not in before, (
            "the route gives up before it has looked in plenum_cafm.documents"
        )

    def test_something_unfindable_anywhere_still_answers_404(self):
        assert "404" in SOURCE
