"""Link existing building certificates to their site, and show what could not be linked.

Certificates ingested before site resolution worked carry a building name but no site link,
so per-site coverage reported one portfolio-wide bucket. This walks the register through the
same resolver ingest now uses.

Dry run by default — it prints what WOULD change and writes nothing:

    python scripts/backfill_certificate_site_links.py
    python scripts/backfill_certificate_site_links.py --apply

Talks to a running svc-operations-intelligence (default http://127.0.0.1:8009), so it uses
the service's own database credentials rather than needing its own.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8009"


class ServiceError(RuntimeError):
    """The service answered, but not with a backfill report."""


def _post(base: str, path: str, params: dict) -> dict:
    """POST and return the report, or raise ServiceError with something worth reading.

    A 404 here almost always means the running container predates this endpoint — the
    service runs a baked image, so new code needs a rebuild. Saying that beats a urllib
    traceback that looks like the script is broken.
    """
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:400]
        except Exception:  # noqa: BLE001 — the status is the point, not the body
            pass
        if exc.code == 404:
            raise ServiceError(
                f"{url}\n"
                f"  HTTP 404 — the service is up but has no backfill endpoint.\n"
                f"  It runs a baked image, so this code is not in the running container yet:\n"
                f"    docker compose -f docker-compose.single-url.local.yml build "
                f"svc-operations-intelligence\n"
                f"    docker compose -f docker-compose.single-url.local.yml up -d "
                f"svc-operations-intelligence\n"
                f"    docker compose -f docker-compose.single-url.local.yml restart gateway-app"
            ) from exc
        raise ServiceError(f"{url}\n  HTTP {exc.code} {exc.reason}\n  {body}") from exc
    except urllib.error.URLError as exc:
        raise ServiceError(
            f"{url}\n  Could not reach the service ({exc.reason}). Is it running, and is "
            f"--base right?"
        ) from exc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE, help="service base URL")
    ap.add_argument("--organization-id", default=None)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the links (without it, nothing is written)",
    )
    args = ap.parse_args()

    params: dict[str, object] = {
        "dry_run": "false" if args.apply else "true",
        "limit": args.limit,
    }
    if args.organization_id:
        params["organization_id"] = args.organization_id

    try:
        out = _post(args.base, "/api/compliance/coverage/backfill-site-links", params)
    except ServiceError as exc:
        print(f"Backfill did not run:\n{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    counts = out.get("counts") or {}
    print(
        f"sites available: {out.get('sites_available')}  "
        f"certificates scanned: {out.get('certificates_scanned')}  "
        f"already linked: {out.get('already_linked')}"
    )
    print(
        f"linked: {counts.get('linked')}  ambiguous: {counts.get('ambiguous')}  "
        f"unmatched: {counts.get('unmatched')}"
    )

    # Print the evidence, not just the verdict. These lines are what a PM reads before
    # writing links into the compliance register, and "Town Hall → Town Hall (reference)"
    # does not show WHICH reference matched WHICH site row — so it cannot be checked.
    for row in out.get("linked") or []:
        ref = row.get("building_reference")
        src = f"{row.get('building_name') or '—'}" + (f" [ref {ref}]" if ref else "")
        print(
            f"  LINK  {src} → {row.get('site_label')} [key {row.get('site_key')}] "
            f"(matched on {row.get('reason')})"
        )
    for row in out.get("ambiguous") or []:
        print(
            f"  SKIP  {row.get('building_name') or '—'} — {row.get('reason')}: "
            f"{', '.join(row.get('candidates') or [])}"
        )
    for row in out.get("unmatched") or []:
        print(f"  NONE  {row.get('building_name') or '—'} — {row.get('reason')}")

    print(out.get("note") or "")
    if not args.apply:
        print("Re-run with --apply to write these links.")


if __name__ == "__main__":
    main()
