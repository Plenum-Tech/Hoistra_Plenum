"""Verify the baseline's independent policies and rejection boundaries."""
import unittest,json
from datetime import date,timedelta
from fastapi import HTTPException
from pydantic import ValidationError
from src.api.homework_identity import TestPrincipal
from src.services.compliance_hw1.dates import classify
from src.services.compliance_hw1.hoi import calculate,meaningful
from src.services.compliance_hw1.fixtures import dataset
from src.services.compliance_hw1.authorization import authorize_row,authorize_action,allowed_tools
from src.services.compliance_hw1.contracts import Query,CertificateValues,Confirmation
from src.agents.compliance_prompt import assemble,validate

class ComplianceBaseline(unittest.TestCase):
    def setUp(self):
        self.pm=TestPrincipal('p','property_manager','hw1-org-a',('hw1-building-a1',))
        self.co=TestPrincipal('c','compliance_officer','hw1-org-a',('hw1-building-a1','hw1-building-a2'))
        self.gu=TestPrincipal('g','general_user','hw1-org-a',('hw1-building-a1',))
    def test_expiry_boundaries(self):
        today=date(2026,10,25)
        for days,label in [(-1,'expired/lapsed'),(0,'expires today'),(1,'critical'),(7,'critical'),(8,'action-required'),(30,'action-required'),(31,'advance reminder'),(90,'advance reminder'),(91,'current')]:
            with self.subTest(days=days):
                result=classify({'expiry_date':str(today+timedelta(days=days))},today)
                self.assertEqual(result['expiry_label'],label)
                self.assertEqual(result['action_window'],0<=days<=30)
    def test_unknown_does_not_become_expired(self):
        self.assertIsNone(classify({},date.today())['days_to_expiry'])
    def test_review_anchor_requires_rule(self):
        self.assertIsNone(classify({'review_date':'2026-10-25'},date(2026,10,25))['days_to_expiry'])
        self.assertEqual(classify({'review_date':'2026-10-25','review_rule':'documented'},date(2026,10,25))['days_to_expiry'],0)
    def test_empty_hoi_zero(self):self.assertEqual(calculate([])['score'],0)
    def test_ten_hoi_groups(self):self.assertEqual(len(calculate([])['group_percentages']),10)
    def test_hoi_extras_no_inflation(self):
        records=dataset()['records'];items=[r for r in records if r['kind']=='hoi_item' and r['buildings']==['hw1-building-a1']]
        extra={'kind':'hoi_item','data':{'accepted':True,'item_type':'asset','slot':999,'fields':{},'source_supported':[]}}
        self.assertEqual(calculate(items),calculate(items+[extra]))
    def test_duplicate_hoi_source_cannot_earn_a_second_slot(self):
        items=[r for r in dataset()['records'] if r['kind']=='hoi_item' and r['buildings']==['hw1-building-a1']]
        original=calculate(items)['score']
        first=next(r for r in items if r['data']['item_type']=='asset' and r['data']['slot']==0)
        second=next(r for r in items if r['data']['item_type']=='asset' and r['data']['slot']==1)
        second['data']['source_id']=first['data']['source_id']
        duplicate=calculate(items)
        self.assertLess(duplicate['score'],original)
        self.assertEqual(duplicate['duplicate_source_references'][0]['reason'],
                         'duplicate source cannot earn a second HOI contribution')
    def test_known_zero_false_are_evidence(self):
        self.assertTrue(meaningful(0));self.assertTrue(meaningful(False));self.assertFalse(meaningful('unknown'))
    def test_unknown_arguments_rejected(self):
        with self.assertRaises(ValidationError):Query(sql='SELECT secret')
    def test_invalid_date_range_rejected(self):
        with self.assertRaises(ValidationError):Query(days_min=30,days_max=0)
    def test_mixed_source_denied(self):
        with self.assertRaises(HTTPException):authorize_row(self.pm,{'org':'hw1-org-a','buildings':['hw1-building-a1','hw1-building-a2'],'data':{'scope_verified':True}},full=True)
    def test_unverified_scope_denied(self):
        with self.assertRaises(HTTPException):authorize_row(self.pm,{'org':'hw1-org-a','buildings':['hw1-building-a1'],'data':{}},full=True)
    def test_general_mutation_denied(self):
        with self.assertRaises(HTTPException):authorize_action(self.gu,'correct_certificate',{'org':'hw1-org-a','buildings':['hw1-building-a1'],'data':{'scope_verified':True}})
    def test_shared_vendor_effect_eligible(self):
        authorize_action(self.pm,'block_vendor',{'org':'hw1-org-a','buildings':['hw1-building-a1','hw1-building-a2'],'data':{'scope_verified':True}})
    def test_no_model_confirmation_tool(self):
        for principal in [self.pm,self.co,self.gu]:
            self.assertFalse(any('confirm' in tool or 'sql' in tool for tool in allowed_tools(principal)))
    def test_officer_no_hoi(self):self.assertNotIn('homework_get_hoi',allowed_tools(self.co))
    def test_general_no_source(self):self.assertNotIn('compliance_get_evidence',allowed_tools(self.gu))
    def test_bad_confirmation_hash_rejected(self):
        with self.assertRaises(ValidationError):Confirmation(version=1,preview_hash='bad',idempotency_key='long-enough')
    def test_manifest_validates(self):validate()

class ComplianceManifestGuards(unittest.TestCase):
    def copied(self):
        import tempfile,shutil
        from src.agents.compliance_prompt import ROOT
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        target=__import__('pathlib').Path(temporary.name)/'skills';shutil.copytree(ROOT,target)
        return target
    def test_changed_instruction_fails_closed(self):
        root=self.copied();manifest=json.loads((root/'compliance-manifest.json').read_text())
        (root/manifest['instructions'][0]['path']).write_text('Changed prompt')
        with self.assertRaises(HTTPException):validate(root)
    def test_extra_role_capability_fails_closed(self):
        root=self.copied();path=root/'compliance-manifest.json';manifest=json.loads(path.read_text())
        manifest['role_tools']['general_user'].append('compliance_get_evidence');path.write_text(json.dumps(manifest))
        with self.assertRaises(HTTPException):validate(root)
    def test_wrong_specification_fails_closed(self):
        root=self.copied();path=root/'compliance-manifest.json';manifest=json.loads(path.read_text())
        manifest['specification_version']='0.38.0';path.write_text(json.dumps(manifest))
        with self.assertRaises(HTTPException):validate(root)

class UnblockBoundaries(unittest.IsolatedAsyncioTestCase):
    async def test_non_certificate_restriction_not_cleared(self):
        from src.services.compliance_hw1.actions import unblock_reasons
        from src.services.compliance_hw1.contracts import ProposalInput
        principal=TestPrincipal('p','property_manager','hw1-org-a',('hw1-building-a1',))
        source={'id':'e','org':'hw1-org-a','buildings':['hw1-building-a1'],'data':{'scope_verified':True,'information_type':'certificate','fields':{'holder_id':'v','expiry_date':'2027-10-25'}}}
        class Repo:
            def __init__(self):self.principal=principal
            async def get(self,table,identifier):
                return {'data':{'date':'2026-10-25'}} if identifier=='clock-hw1-org-a' else source
        target={'id':'v','data':{'blocking_reasons':[{'resolved':False,'category':'commercial'}]}}
        with self.assertRaises(HTTPException) as error:
            await unblock_reasons(Repo(),target,ProposalInput(action='unblock_vendor',entity_id='v',source_id='e',reason='Current evidence'))
        self.assertEqual(error.exception.status_code,409)

    async def test_expired_replacement_not_accepted(self):
        from src.services.compliance_hw1.actions import unblock_reasons
        from src.services.compliance_hw1.contracts import ProposalInput
        principal=TestPrincipal('p','property_manager','hw1-org-a',('hw1-building-a1',))
        class Repo:
            def __init__(self):self.principal=principal
            async def get(self,table,identifier):
                if identifier=='clock-hw1-org-a':return {'data':{'date':'2026-10-25'}}
                return {'org':'hw1-org-a','buildings':['hw1-building-a1'],'data':{'scope_verified':True,'information_type':'certificate','fields':{'holder_id':'v','expiry_date':'2026-10-24'}}}
        with self.assertRaises(HTTPException) as error:
            await unblock_reasons(Repo(),{'id':'v','data':{'blocking_reasons':[]}},ProposalInput(action='unblock_vendor',entity_id='v',source_id='e',reason='Expired evidence'))
        self.assertEqual(error.exception.status_code,409)
