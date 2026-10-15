"""The certificate download reads Blob with the account key and never fetches a foreign URL."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.engines.compliance.certificate_file import download_filename, parse_blob_url

ACC = "plenumstorage"


def test_a_url_on_this_account_gives_container_and_blob():
    assert parse_blob_url(
        "https://plenumstorage.blob.core.windows.net/plenum-agentic-ai-attachments/pdf-raw/org/2026-09/x%20y.pdf",
        account=ACC,
    ) == ("plenum-agentic-ai-attachments", "pdf-raw/org/2026-09/x y.pdf")


def test_a_foreign_host_is_never_fetched():
    for url in (
        "https://evil.example/plenum-agentic-ai-attachments/a.pdf",
        "https://plenumstorage.blob.core.windows.net.evil.example/c/a.pdf",
        "http://plenumstorage.blob.core.windows.net/c/a.pdf",
        "https://otheraccount.blob.core.windows.net/c/a.pdf",
        "file:///etc/passwd",
    ):
        assert parse_blob_url(url, account=ACC) is None, url


def test_no_account_configured_means_nothing_is_fetched():
    assert parse_blob_url("https://plenumstorage.blob.core.windows.net/c/a.pdf", account=None) is None


def test_a_container_with_no_blob_name_is_refused():
    assert parse_blob_url("https://plenumstorage.blob.core.windows.net/c/", account=ACC) is None


def test_the_filename_drops_the_storage_uuid_prefix_and_header_breakers():
    cert = SimpleNamespace(id=uuid4())
    assert download_filename(cert, "pdf-raw/1dbf31bd-7bea-4dca-98a0-113878c05bd3_EPC Harbour.pdf", None) == "EPC Harbour.pdf"
    assert download_filename(cert, "x/blob.pdf", 'Gas "Safe"\r\n.pdf') == "Gas Safe.pdf"


from src.engines.compliance.certificate_file import _same_company


def _cert(org="o1", bld="b1"):
    return SimpleNamespace(id=uuid4(), organization_id=org, org_id=None, building_id=bld)


def test_a_document_of_another_company_is_never_served():
    assert _same_company("o1", None, _cert()) is True
    assert _same_company("o2", None, _cert()) is False
    assert _same_company("o2", "b1", _cert()) is False        # a matching building does not override a named company


def test_a_document_with_no_company_is_served_only_on_the_certificates_own_building():
    assert _same_company(None, "b1", _cert(), building_is_ours=True) is True
    assert _same_company(None, "b9", _cert(), building_is_ours=True) is False
    assert _same_company(None, None, _cert()) is False
    assert _same_company(None, None, _cert(org=None, bld=None)) is True


def test_a_certificate_pointed_at_another_companys_building_gains_nothing():
    # building_id on the certificate is client-writable: it only counts when the buildings
    # table says the building is the certificate's company's.
    assert _same_company(None, "b1", _cert(), building_is_ours=False) is False


from src.engines.compliance.certificate_file import choose_document

_BLOB = "https://plenumstorage.blob.core.windows.net/plenum-agentic-ai-attachments/doc-rag/x_CP17.pdf"


def _ing(doc_id="d1", blob=_BLOB, org=None, bld=None):
    return ("ingestion_documents", {"id": doc_id, "blob_url": blob, "name": "s_CP17.pdf", "org": org, "bld": bld})


def _graph(doc_id="d1", blob=None, org=None, bld="b1"):
    return ("documents", {"id": doc_id, "blob_url": blob, "name": "CP17.pdf", "org": org, "bld": bld})


def test_the_uploaded_original_is_served_when_its_graph_row_is_this_companys():
    # doc-rag's ingestion_documents has no company or building column, so the row holding
    # the PDF could never prove itself; the graph row with the same id is on the
    # certificate's own building and vouches for it. Before this, the PDF was refused and
    # the vault downloaded the extracted text as "CP17….pdf.txt".
    doc, refused = choose_document([_ing(), _graph()], _cert(), building_is_ours=True)
    assert doc["table"] == "ingestion_documents" and doc["blob_url"] == _BLOB
    assert refused is False


def test_an_ingestion_row_with_no_graph_row_to_vouch_for_it_is_still_refused():
    doc, refused = choose_document([_ing()], _cert(), building_is_ours=True)
    assert doc is None and refused is True


def test_a_graph_row_on_another_companys_building_vouches_for_nothing():
    # building_is_ours False: the certificate's building_id is not this company's.
    doc, refused = choose_document([_ing(), _graph()], _cert(), building_is_ours=False)
    assert doc is None and refused is True


def test_a_graph_row_only_vouches_for_its_own_id():
    doc, _ = choose_document([_ing(doc_id="victim"), _graph(doc_id="d1")], _cert(), building_is_ours=True)
    assert doc["table"] == "documents" and doc["id"] == "d1"


def test_an_ingestion_row_naming_another_company_is_refused_even_when_vouched():
    doc, refused = choose_document([_ing(org="o2"), _graph()], _cert(), building_is_ours=True)
    assert doc["table"] == "documents" and refused is True


import pytest

from src.engines.compliance.certificate_file import CertificateFileError, open_blob


class _NotFound(Exception):
    pass


def _fake_client(*, parts=(b"%PDF-1.7 ", b"body"), fail_at=None, content_type="application/pdf"):
    state = {"closed": False, "asked": None}

    class Downloader:
        properties = SimpleNamespace(content_settings=SimpleNamespace(content_type=content_type),
                                     size=sum(len(c) for c in parts))

        def chunks(self):
            yield from parts

    class Client:
        @classmethod
        def from_connection_string(cls, conn):
            if fail_at == "construct":
                # What the async client did in the image: no aiohttp, so no transport.
                raise ImportError("Unable to create async transport. Please check aiohttp is installed.")
            return cls()

        def get_blob_client(self, container, blob):
            state["asked"] = (container, blob)
            return self

        def download_blob(self):
            if fail_at == "missing":
                raise _NotFound()
            return Downloader()

        def close(self):
            state["closed"] = True

    return Client, state


async def test_the_blob_is_streamed_whole_and_the_client_closed_after():
    client, state = _fake_client()
    ctype, size, it = await open_blob(client, _NotFound, "conn", "c", "doc-rag/x_CP17.pdf")
    assert state["asked"] == ("c", "doc-rag/x_CP17.pdf")
    assert (ctype, size) == ("application/pdf", 13)
    assert b"".join(it) == b"%PDF-1.7 body"
    assert state["closed"] is True


async def test_a_client_that_cannot_be_built_is_a_502_not_an_unhandled_500():
    client, _ = _fake_client(fail_at="construct")
    with pytest.raises(CertificateFileError) as e:
        await open_blob(client, _NotFound, "conn", "c", "b.pdf")
    assert e.value.status == 502


async def test_a_blob_gone_from_storage_is_a_404_and_the_client_is_closed():
    client, state = _fake_client(fail_at="missing")
    with pytest.raises(CertificateFileError) as e:
        await open_blob(client, _NotFound, "conn", "c", "b.pdf")
    assert e.value.status == 404 and state["closed"] is True


async def test_a_blob_with_no_content_type_is_served_as_octet_stream():
    client, _ = _fake_client(content_type=None)
    ctype, _, _ = await open_blob(client, _NotFound, "conn", "c", "b.pdf")
    assert ctype == "application/octet-stream"
