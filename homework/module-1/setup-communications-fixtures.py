"""Append fictional evidence for an API-onboarded milestone building. No historical edits."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents'))
from src.services.compliance_hw1 import fixtures

def quote(value):return "'"+str(value).replace("'","''")+"'"

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--building',required=True);parser.add_argument('--resolve-contact',action='store_true');args=parser.parse_args()
    if not args.building.startswith('hw1-building-new-') or len(args.building)!=49:raise SystemExit('API-onboarded fictional building required')
    fixtures.BUILDINGS={args.building:'hw1-org-a'}
    data=fixtures.dataset()
    data['records'].append({'id':args.building+'-portfolio-manager-contact','kind':'contact','org':'hw1-org-a',
        'buildings':[args.building],'data':{'scope_verified':True,'manager_role':'portfolio_manager',
            'contact_type':'team','email':None,'purposes':['team_notification']}})
    # Leave the API-created profile, manifest and portfolio membership intact.
    statements=['BEGIN;']
    for table,rows in [('records',data['records']),('sources',data['sources'])]:
        for row in rows:
            buildings='ARRAY['+','.join(quote(b) for b in row['buildings'])+']::text[]'
            statements.append('INSERT INTO hw1_compliance.'+table+' (id,kind,org,buildings,data) VALUES ('+
                ','.join([quote(row['id']),quote(row['kind']),quote(row['org']),buildings,quote(json.dumps(row['data']))+'::jsonb'])+') ON CONFLICT (id) DO NOTHING;')
    # These source-backed fixtures exercise permitted manager/team transport outcomes.
    for mode in ['success','fail','fail_once','unknown','missing']:
        d={'scope_verified':True,'email':None if mode=='missing' else mode+'@homework.invalid','simulation':mode,
           'contact_type':'team','purposes':['team_notification','work_order_request','contract_review','energy_review','certificate_renewal','inspection_booking']}
        statements.append('INSERT INTO hw1_compliance.records (id,kind,org,buildings,data) VALUES ('+
            ','.join([quote(args.building+'-mail-'+mode),quote('contact'),quote('hw1-org-a'),
            'ARRAY['+quote(args.building)+']::text[]',quote(json.dumps(d))+'::jsonb'])+') ON CONFLICT (id) DO NOTHING;')
    # Portfolio manager assignment derives from the existing trusted portfolio record,
    # not invented contact creation. Link its existing contact to this fictional building.
    if args.resolve_contact:
        statements.append("UPDATE hw1_compliance.records SET data=jsonb_set(data,'{email}','\"recovered-portfolio@homework.invalid\"'::jsonb) WHERE id="+
            quote(args.building+'-portfolio-manager-contact')+" AND kind='contact';")
    statements.append('COMMIT;')
    r=subprocess.run(['docker','exec','-i','hoistra-hw1-postgres-1','psql','-X','-v','ON_ERROR_STOP=1','-U','hoistra_hw1','-d','hoistra_hw1'],
        input='\n'.join(statements),capture_output=True,text=True)
    if r.returncode:raise SystemExit('Fictional communications fixture append failed')
    print(json.dumps({'fictional_building':args.building,'originals_overwritten':False}))

if __name__=='__main__':main()
