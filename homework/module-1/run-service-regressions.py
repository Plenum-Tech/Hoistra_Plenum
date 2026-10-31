#!/usr/bin/env python3
"""Run the actual service suite with private isolated DB configuration, never dummy URLs."""
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SERVICE=ROOT/'apps/backend/cafm-connector-service-final/svc-deepagents'
# This script is invoked using the service virtual environment, which owns its dependencies.
from dotenv import dotenv_values
from sqlalchemy.engine import make_url
config=ROOT/'homework/module-1/runtime/.env'
if config.stat().st_mode & 0o077:raise SystemExit('Private runtime configuration permissions too broad')
env=dotenv_values(config);os.environ.update({k:v for k,v in env.items() if v is not None})
for key in ['HW1_READ_DB_URL','HW1_COMPLIANCE_DB_URL']:
 os.environ[key]=make_url(env[key]).set(host='127.0.0.1',port=15432).render_as_string(hide_password=False)
os.environ['DB_URL']=make_url('postgresql+asyncpg://hoistra_hw1@127.0.0.1:15432/hoistra_hw1').set(password=env['HW1_POSTGRES_PASSWORD']).render_as_string(hide_password=False)
os.chdir(SERVICE)
raise SystemExit(__import__('pytest').main(['-q']))
