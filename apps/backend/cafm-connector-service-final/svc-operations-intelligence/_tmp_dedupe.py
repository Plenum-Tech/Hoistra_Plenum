import asyncio, asyncpg, os

async def main():
    url = os.environ["DB_URL"].replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(url, ssl="require")
    r = await c.execute(
        "UPDATE plenum_cafm.approvals_queue_items SET status='dismissed', updated_at=NOW() "
        "WHERE id='dc4980ca-baf5-4e40-b046-2b5486e47121'::uuid"
    )
    print(r)
    rows = await c.fetch(
        "SELECT id::text, summary, email_draft->>'subject' AS subject "
        "FROM plenum_cafm.approvals_queue_items WHERE status='pending' ORDER BY created_at DESC"
    )
    for row in rows:
        print(dict(row))
    await c.close()

asyncio.run(main())
