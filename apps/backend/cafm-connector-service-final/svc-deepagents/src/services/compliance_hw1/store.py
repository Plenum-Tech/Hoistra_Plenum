"""RLS-scoped transactions on a dedicated non-owner homework database role."""
import json
import os
from contextlib import asynccontextmanager
from functools import lru_cache
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi import HTTPException
from ...api.homework_identity import TestPrincipal

TABLES = {'records', 'sources', 'proposals', 'history', 'outbox', 'events', 'sessions'}

class Repository:
    def __init__(self, session, principal):
        self.session, self.principal = session, principal

    async def list(self, table, kind=None, lock=False):
        if table not in TABLES:
            raise ValueError('Unknown internal table')
        # Identifiers come from a fixed server allowlist; all values are parameters.
        where = 'org = :org' + (' AND kind = :kind' if kind else '')
        sql = f'SELECT id, kind, org, buildings, data FROM hw1_compliance.{table} WHERE {where} ORDER BY id'
        if lock:
            sql += ' FOR UPDATE'
        rows = await self.session.execute(text(sql), {'org': self.principal.organization_id, 'kind': kind})
        return [dict(r) for r in rows.mappings()]

    async def get(self, table, identifier, lock=False):
        if table not in TABLES:
            raise ValueError('Unknown internal table')
        sql = f'SELECT id, kind, org, buildings, data FROM hw1_compliance.{table} WHERE org=:org AND id=:id'
        if lock:
            sql += ' FOR UPDATE'
        row = (await self.session.execute(text(sql), {'org': self.principal.organization_id, 'id': identifier})).mappings().first()
        if not row:
            raise HTTPException(404, 'Record unavailable')
        return dict(row)

    async def scoped(self,table,scope,kinds,identifier=None):
        """Apply trusted building/content predicates before fetching new adapter content."""
        if table not in TABLES: raise ValueError('Unknown internal table')
        sql=f'''SELECT id,kind,org,buildings,data FROM hw1_compliance.{table}
            WHERE org=:org AND cardinality(buildings)>0 AND buildings <@ CAST(:scope AS text[])
            AND (CAST(:identifier AS text) IS NULL OR id=CAST(:identifier AS text))
            AND kind=ANY(CAST(:kinds AS text[])) AND (data->>'sensitive') IS DISTINCT FROM 'true' '''
        if table=='sources': sql+=" AND data->>'scope_verified'='true' "
        sql+=' ORDER BY id'
        rows=(await self.session.execute(text(sql),{'org':self.principal.organization_id,'scope':list(scope),
            'kinds':list(kinds),'identifier':identifier})).mappings().all()
        if identifier is not None and not rows: raise HTTPException(404,'Record unavailable')
        return [dict(row) for row in rows]

    async def outbox_page(self,scope,limit,offset,after_id=None,include_issues=True):
        """Filter/count/page in SQL; retained org-wide messages never enter a role response."""
        role=self.principal.role
        predicate="""o.org=:org AND cardinality(o.buildings)>0 AND o.buildings <@ CAST(:scope AS text[])
            AND o.data->>'scope_verified'='true' AND (o.data->>'sensitive') IS DISTINCT FROM 'true'"""
        if len(scope)==1:
            predicate+=" AND hw1_compliance.scope_fingerprint(o.buildings)=hw1_compliance.scope_fingerprint(CAST(:scope AS text[]))"
        if role=='general_user':predicate+=" AND o.data->>'actor'=:actor"
        elif role=='compliance_officer':
            predicate+=""" AND o.data->>'category'='compliance'
                AND (o.data #>> '{message,purpose}') IS DISTINCT FROM 'team_notification'
                AND ((o.data #>> '{message,action}') IS DISTINCT FROM 'email'
                    OR o.data #>> '{message,purpose}' IN ('certificate_renewal','inspection_booking'))"""
        params={'org':self.principal.organization_id,'scope':list(scope),'actor':self.principal.user_id,'limit':limit,'offset':offset}
        if after_id is not None and offset:
            raise HTTPException(422,'Use either cursor or offset pagination')
        count=(await self.session.execute(text('SELECT count(*) FROM hw1_compliance.outbox o WHERE '+predicate),params)).scalar_one() if after_id is None else None
        page_predicate=predicate
        if after_id is not None:
            page_predicate+=' AND o.id>:after_id'
            params.update(after_id=after_id,limit=limit+1)
        page=(await self.session.execute(text('SELECT o.id,o.kind,o.org,o.buildings,o.data FROM hw1_compliance.outbox o WHERE '+page_predicate+' ORDER BY o.id LIMIT :limit OFFSET :offset'),params)).mappings().all()
        issues=[]
        if include_issues and role!='general_user':
            allowed="true" if role in {'property_manager','portfolio_manager'} else "EXISTS (SELECT 1 FROM hw1_compliance.outbox o WHERE o.id=h.data->>'outbox_id' AND "+predicate+")"
            query="""SELECT h.data FROM hw1_compliance.history h WHERE h.org=:org AND h.kind='notification_issue'
                AND cardinality(h.buildings)>0 AND h.buildings <@ CAST(:scope AS text[]) AND ("""+allowed+") ORDER BY h.id"
            if len(scope)==1:
                query=query.replace(' ORDER BY h.id', ' AND hw1_compliance.scope_fingerprint(h.buildings)=hw1_compliance.scope_fingerprint(CAST(:scope AS text[])) ORDER BY h.id')
            issues=[r[0] for r in (await self.session.execute(text(query),params)).all()]
        return [dict(r) for r in page],count,issues

    async def usage_snapshot(self,scope):
        """One SQL statement captures events/backlog/contact configuration consistently."""
        queries=[]
        for table in ['history','proposals','records']:
            predicate=("(kind='building' AND id=ANY(CAST(:scope AS text[]))) OR "
                       "(kind='contact' AND CAST(:scope AS text[]) <@ buildings)" if table=='records' else
                       "cardinality(buildings)>0 AND buildings <@ CAST(:scope AS text[])")
            if table=='history':
                # Notification/HOI evidence grows independently of measured usage.
                # Loading its full bodies can exhaust the worker for portfolio reports.
                # Retain every usage input and immutable prior report for exact replay.
                predicate += " AND kind IN ('usage_measurement','trace','unified_trace','usage_failure','ingestion','ingestion_correction','ingestion_conflict_resolution','building_onboarding','decision','usage_report')"
            queries.append(f"SELECT '{table}' AS collection,id,kind,org,buildings,data FROM hw1_compliance.{table} WHERE org=:org AND ({predicate})")
        rows=(await self.session.execute(text(' UNION ALL '.join(queries)),
            {'org':self.principal.organization_id,'scope':list(scope)})).mappings().all()
        result={table:[] for table in ['history','proposals','records']}
        for row in rows:
            item=dict(row);collection=item.pop('collection');result[collection].append(item)
        return result

    async def insert(self, table, kind, buildings, data, identifier=None):
        if table not in TABLES:
            raise ValueError('Unknown internal table')
        identifier = identifier or str(uuid4())
        await self.session.execute(text(f'''INSERT INTO hw1_compliance.{table}
            (id,kind,org,buildings,data) VALUES (:id,:kind,:org,:buildings,CAST(:data AS jsonb))'''),
            {'id':identifier,'kind':kind,'org':self.principal.organization_id,
             'buildings':list(buildings),'data':json.dumps(data,default=str)})
        return identifier

    async def update(self, table, row, data):
        # Immutable evidence/history/events are never updated through application access.
        if table not in {'records', 'proposals', 'outbox', 'sessions'}:
            raise ValueError('Immutable table')
        await self.session.execute(text(f'UPDATE hw1_compliance.{table} SET data=CAST(:data AS jsonb) WHERE org=:org AND id=:id'),
            {'id':row['id'],'org':self.principal.organization_id,'data':json.dumps(data,default=str)})

    async def audit(self, operation, outcome, target=None, request_id=None):
        # Only safe identifiers and outcomes are audited, never messages or document bodies.
        await self.session.execute(text('''INSERT INTO hw1_compliance.audit
            (id,org,actor,role,operation,outcome,target,request_id)
            VALUES (:id,:org,:actor,:role,:operation,:outcome,CAST(:target AS jsonb),:request)'''),
            {'id':str(uuid4()),'org':self.principal.organization_id,'actor':self.principal.user_id,
             'role':self.principal.role,'operation':operation,'outcome':outcome,
             'target':json.dumps(target or {}),'request':request_id or str(uuid4())})

class Store:
    def __init__(self, url):
        parsed = make_url(url)
        if parsed.drivername != 'postgresql+asyncpg' or parsed.username != 'hw1_compliance_app' or not parsed.password or parsed.database != 'hoistra_hw1':
            raise ValueError('Dedicated HW1_COMPLIANCE_DB_URL required')
        self.engine = create_async_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self, principal, repeatable=False):
        if not isinstance(principal, TestPrincipal):
            raise HTTPException(401, 'Trusted principal required')
        async with self.factory() as session:
            async with session.begin():
                if repeatable:
                    await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
                await session.execute(text("SELECT set_config('hw1.org',:org,true)"), {'org':principal.organization_id})
                yield Repository(session, principal)

    async def validate_schema(self):
        async with self.engine.connect() as conn:
            row=(await conn.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"))).first()
            if not row or row[0] or row[1]:
                raise ValueError('Compliance runtime role must not bypass isolation')
            for table in TABLES:
                await conn.execute(text(f'SELECT id,kind,org,buildings,data FROM hw1_compliance.{table} LIMIT 0'))
            for table in ['sources','history','events','audit']:
                denied=(await conn.execute(text("SELECT has_table_privilege(current_user,:table,'UPDATE,DELETE')"),
                        {'table':'hw1_compliance.'+table})).scalar_one()
                if denied:
                    raise ValueError('Immutable evidence/audit privileges are too broad')

    async def audit(self, principal, operation, outcome, target=None, request_id=None):
        async with self.transaction(principal) as repo:
            await repo.audit(operation,outcome,target,request_id)

@lru_cache(maxsize=1)
def runtime_store():
    value = os.environ.get('HW1_COMPLIANCE_DB_URL','')
    if not value:
        raise ValueError('HW1_COMPLIANCE_DB_URL is required')
    return Store(value)
