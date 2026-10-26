"""Every seed requirement quotes its implementing doc verbatim (spec §8.2). A doc outside svc-deepagents exists
only in a repo checkout: those implementations are skipped here with a reason, never failed."""
from __future__ import annotations

from pathlib import Path

import pytest

from trace_lab import requirements


def test_codes_are_unique_well_formed_and_match_their_area():
    seed = requirements.load_seed()
    codes = [r["code"] for r in seed]
    assert len(codes) == len(set(codes)) == 23
    for r in seed:
        assert requirements.CODE_RE.match(r["code"])
        assert r["code"].split("-")[0] == r["area"]
        assert 10 <= len(r["statement"]) <= 600
        assert r["implementations"], r["code"]


@pytest.mark.parametrize("code,impl", [(r["code"], i) for r in requirements.load_seed() for i in r["implementations"]])
def test_every_seed_quote_is_found_in_its_doc(code, impl):
    path, in_service = requirements.doc_path(impl["doc"])
    if path is None or not path.is_file():
        if in_service:
            pytest.fail(f"{code}: {impl['doc']} is missing from the service tree")
        pytest.skip(f"{code}: {impl['doc']} is outside svc-deepagents and not present here")
    assert requirements.quote_status(impl) == "ok", f"{code}: quote drifted in {impl['doc']}"


def test_a_missing_quote_is_drift_and_a_doc_outside_the_image_is_unverifiable(tmp_path, monkeypatch):
    victim = tmp_path / "svc" / "skills" / "x.md"
    victim.parent.mkdir(parents=True)
    victim.write_text("Lead with the answer.\n", encoding="utf-8")
    monkeypatch.setattr(requirements, "SERVICE_ROOT", tmp_path / "svc")
    monkeypatch.setattr(requirements, "_repo_root", lambda: None)
    prefix = requirements.SERVICE_PREFIX
    assert requirements.quote_status({"doc": prefix + "skills/x.md", "quote": "Lead with the answer."}) == "ok"
    assert requirements.quote_status({"doc": prefix + "skills/x.md", "quote": "Never show raw ids."}) == "drift"
    assert requirements.quote_status({"doc": prefix + "skills/gone.md", "quote": "anything at all"}) == "drift"
    assert requirements.quote_status({"doc": "specs/x/spec.md", "quote": "anything at all"}) == "unverifiable_in_image"


def test_paths_that_climb_out_of_the_repo_are_never_read(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("password=hunter2\n", encoding="utf-8")
    monkeypatch.setattr(requirements, "_repo_root", lambda: tmp_path / "repo")
    for doc in ("../secret.txt", str(secret), "specs/../../secret.txt"):
        assert requirements.safe_doc(doc) is False
        assert requirements.quote_status({"doc": doc, "quote": "password=hunter2"}) == "unverifiable_in_image"


def test_the_service_root_is_the_deepagents_tree_beside_this_service():
    """The docs quoted live in svc-deepagents, a sibling folder (the image copies it beside Trace Lab)."""
    assert requirements.SERVICE_ROOT.name == "svc-deepagents"
    assert (requirements.SERVICE_ROOT / "skills").is_dir()
    assert requirements.SERVICE_ROOT.parent == Path(requirements.__file__).resolve().parents[2]


def test_the_repo_root_is_none_inside_an_image_without_an_apps_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(requirements, "SERVICE_ROOT", tmp_path / "app" / "svc-deepagents")
    assert requirements._repo_root() is None
    deep = tmp_path / "repo" / "apps" / "backend" / "cafm-connector-service-final" / "svc-deepagents"
    deep.mkdir(parents=True)
    monkeypatch.setattr(requirements, "SERVICE_ROOT", deep)
    assert requirements._repo_root() == tmp_path / "repo"


def test_a_repo_doc_outside_the_service_is_read_when_the_checkout_has_it(tmp_path, monkeypatch):
    spec = tmp_path / "specs" / "x" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("System MUST cap the score at 60.\n", encoding="utf-8")
    monkeypatch.setattr(requirements, "_repo_root", lambda: tmp_path)
    assert requirements.quote_status({"doc": "specs/x/spec.md", "quote": "MUST cap the score at 60"}) == "ok"
    assert requirements.quote_status({"doc": "specs/x/spec.md", "quote": "MUST cap the score at 70"}) == "drift"
    assert requirements.quote_status({"doc": "specs/x/missing.md", "quote": "anything at all"}) == "unverifiable_in_image"


def test_seed_levels_and_areas_are_known_and_the_loader_refuses_a_bad_row(tmp_path, monkeypatch):
    for r in requirements.load_seed():
        assert r["level"] in requirements.LEVELS and r["area"] in requirements.AREAS
    bad = tmp_path / "seed.json"
    bad.write_text('[{"code": "GEN-1", "statement": "x", "area": "COMP", "level": "technical", "implementations": []}]')
    monkeypatch.setattr(requirements, "SEED_FILE", bad)
    with pytest.raises(ValueError):
        requirements.load_seed()


def test_a_blank_quote_proves_nothing(tmp_path, monkeypatch):
    victim = tmp_path / "svc" / "skills" / "x.md"
    victim.parent.mkdir(parents=True)
    victim.write_text("Lead with the answer.\n", encoding="utf-8")
    monkeypatch.setattr(requirements, "SERVICE_ROOT", tmp_path / "svc")
    for quote in ("", "   ", None):
        assert requirements.quote_status({"doc": requirements.SERVICE_PREFIX + "skills/x.md", "quote": quote}) == "drift"


# ---- fix round 1: a doc path can never reach a file outside the root it claims to be under -------------------------

@pytest.fixture
def reads(monkeypatch):
    """Every path the module reads text from, so a test can say 'never read'."""
    seen: list[Path] = []
    real = Path.read_text

    def spy(self, *a, **kw):
        seen.append(self)
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    return seen


def _outside_secret(tmp_path, name="secret.md"):
    secret = tmp_path / "outside" / name
    secret.parent.mkdir(parents=True)
    secret.write_text("password=hunter2\n", encoding="utf-8")
    return secret


def test_a_second_slash_after_the_service_prefix_cannot_name_an_absolute_file(tmp_path, monkeypatch, reads):
    """SERVICE_PREFIX + '/abs/path' used to slice to '/abs/path', and pathlib then discarded SERVICE_ROOT."""
    secret = _outside_secret(tmp_path)
    monkeypatch.setattr(requirements, "SERVICE_ROOT", tmp_path / "svc")
    monkeypatch.setattr(requirements, "_repo_root", lambda: None)
    (tmp_path / "svc").mkdir()
    for doc in (requirements.SERVICE_PREFIX + str(secret), requirements.SERVICE_PREFIX + "/" + str(secret),
                requirements.SERVICE_PREFIX.rstrip("/") + "//" + str(secret).lstrip("/")):
        path, in_service = requirements.doc_path(doc)
        assert in_service is True
        assert path != secret
        assert path is None or (tmp_path / "svc").resolve() in path.resolve().parents
        assert requirements.quote_status({"doc": doc, "quote": "password=hunter2"}) in ("drift", "unverifiable_in_image")
    assert secret not in reads


def test_a_doubled_slash_in_a_repo_path_stays_inside_the_repo(tmp_path, monkeypatch, reads):
    secret = _outside_secret(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(requirements, "_repo_root", lambda: repo)
    for doc in ("specs//" + str(secret), "specs/" + str(secret), "specs///" + str(secret).lstrip("/")):
        assert requirements.quote_status({"doc": doc, "quote": "password=hunter2"}) == "unverifiable_in_image"
    assert secret not in reads
    inside = repo / "specs" / "x" / "spec.md"
    inside.parent.mkdir(parents=True)
    inside.write_text("System MUST cap the score at 60.\n", encoding="utf-8")
    assert requirements.quote_status({"doc": "specs//x//spec.md", "quote": "MUST cap the score at 60"}) == "ok"


def test_a_symlinked_file_that_leaves_the_root_is_never_read(tmp_path, monkeypatch, reads):
    secret = _outside_secret(tmp_path)
    svc, repo = tmp_path / "svc", tmp_path / "repo"
    (svc / "skills").mkdir(parents=True)
    (repo / "specs").mkdir(parents=True)
    (svc / "skills" / "link.md").symlink_to(secret)
    (repo / "specs" / "link.md").symlink_to(secret)
    monkeypatch.setattr(requirements, "SERVICE_ROOT", svc)
    monkeypatch.setattr(requirements, "_repo_root", lambda: repo)
    assert requirements.quote_status({"doc": requirements.SERVICE_PREFIX + "skills/link.md",
                                      "quote": "password=hunter2"}) == "drift"
    assert requirements.quote_status({"doc": "specs/link.md", "quote": "password=hunter2"}) == "unverifiable_in_image"
    assert secret not in reads and secret.resolve() not in reads


def test_a_symlinked_folder_that_leaves_the_root_is_never_read(tmp_path, monkeypatch, reads):
    outside = _outside_secret(tmp_path).parent
    svc, repo = tmp_path / "svc", tmp_path / "repo"
    svc.mkdir()
    repo.mkdir()
    (svc / "skills").symlink_to(outside, target_is_directory=True)
    (repo / "specs").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(requirements, "SERVICE_ROOT", svc)
    monkeypatch.setattr(requirements, "_repo_root", lambda: repo)
    assert requirements.quote_status({"doc": requirements.SERVICE_PREFIX + "skills/secret.md",
                                      "quote": "password=hunter2"}) == "drift"
    assert requirements.quote_status({"doc": "specs/secret.md", "quote": "password=hunter2"}) == "unverifiable_in_image"
    assert not reads


def test_a_symlink_that_stays_inside_the_root_still_reads(tmp_path, monkeypatch):
    svc = tmp_path / "svc"
    (svc / "skills").mkdir(parents=True)
    (svc / "real.md").write_text("Lead with the answer.\n", encoding="utf-8")
    (svc / "skills" / "alias.md").symlink_to(svc / "real.md")
    monkeypatch.setattr(requirements, "SERVICE_ROOT", svc)
    assert requirements.quote_status({"doc": requirements.SERVICE_PREFIX + "skills/alias.md",
                                      "quote": "Lead with the answer."}) == "ok"


@pytest.mark.parametrize("doc", [
    ".env", "specs/.env", "apps/backend/.git/config", ".git/config", "specs/.hidden/x.md", "specs/.env.md",
    "proc/self/environ", "etc/passwd", "specs/x/spec", "specs/x/spec.MARKDOWN", "specs/x/key.pem",
    "specs/x/\x00.md", "specs/x\\spec.md", "~/spec.md", "", "specs/", "/specs/x.md", "specs/../x.md",
])
def test_only_plain_text_docs_outside_hidden_folders_are_accepted(doc):
    assert requirements.safe_doc(doc) is False


@pytest.mark.parametrize("doc", [
    "specs/x/spec.md", "apps/backend/x/skills/core.md", "apps/backend/x/src/agents/a.py", "docs/data.json",
    "docs/notes.txt", "specs//x//spec.md", "specs/x/SPEC.MD",
])
def test_ordinary_docs_are_accepted(doc):
    assert requirements.safe_doc(doc) is True


def test_a_hidden_file_inside_the_checkout_is_never_read(tmp_path, monkeypatch, reads):
    (tmp_path / ".env").write_text("API_KEY=sk-secret\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    monkeypatch.setattr(requirements, "_repo_root", lambda: tmp_path)
    for doc in (".env", ".git/config"):
        assert requirements.quote_status({"doc": doc, "quote": "API_KEY=sk-secret"}) == "unverifiable_in_image"
    assert not reads


def test_a_symlink_loop_is_refused_not_crashed(tmp_path, monkeypatch):
    svc = tmp_path / "svc"
    (svc / "skills").mkdir(parents=True)
    (svc / "skills" / "a.md").symlink_to(svc / "skills" / "b.md")
    (svc / "skills" / "b.md").symlink_to(svc / "skills" / "a.md")
    monkeypatch.setattr(requirements, "SERVICE_ROOT", svc)
    doc = requirements.SERVICE_PREFIX + "skills/a.md"
    assert requirements.quote_status({"doc": doc, "quote": "anything at all"}) == "drift"
