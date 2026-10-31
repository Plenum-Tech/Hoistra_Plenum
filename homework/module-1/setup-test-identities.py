"""Configure isolated fixtures without displaying session credentials."""
import hashlib
import json
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Derive the checkout so the isolated setup is reproducible outside the original VPS.
root = Path(__file__).resolve().parents[2]
git_root = Path(subprocess.check_output(
    ['git', 'rev-parse', '--show-toplevel'], cwd=root, text=True).strip()).resolve()
if git_root != root:
    raise RuntimeError('Homework setup must run from its containing Git checkout')
module = root / 'homework/module-1'
runtime = module / 'runtime'
identity_file = runtime / 'test-identities.json'
token_file = runtime / 'test-session-tokens.json'
if not identity_file.exists():
    users = [
        ('hw1-general', 'general_user', 'hw1-org-a', ['hw1-building-a1']),
        ('hw1-property', 'property_manager', 'hw1-org-a', ['hw1-building-a1']),
        ('hw1-portfolio', 'portfolio_manager', 'hw1-org-a', ['hw1-building-a1', 'hw1-building-a2']),
        ('hw1-compliance', 'compliance_officer', 'hw1-org-a', []),
        ('hw1-other-property', 'property_manager', 'hw1-org-b', ['hw1-building-b1']),
    ]
    tokens = {user[0]: secrets.token_urlsafe(32) for user in users}
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    user_rows = [dict(zip(('user_id', 'role', 'organization_id', 'building_ids'), row)) for row in users]
    for row in user_rows:
        if row['role'] == 'portfolio_manager':
            row['portfolio_id'] = row['organization_id'] + '-default-portfolio'
    data = {'buildings': {'hw1-building-a1': 'hw1-org-a', 'hw1-building-a2': 'hw1-org-a',
                          'hw1-building-b1': 'hw1-org-b'},
            'users': user_rows,
            'sessions': [{'user_id': user, 'token_sha256': hashlib.sha256(token.encode()).hexdigest(),
                          'expires_at': expiry} for user, token in tokens.items()]}
    # Private tokens are never committed, copied into images or printed in logs.
    for path, content in ((identity_file, data), (token_file, tokens)):
        with path.open('x') as output:
            path.chmod(0o600)
            json.dump(content, output, indent=2)
compose = module / 'docker-compose.hw1.yml'
text = compose.read_text()
anchor = '      DB_URL: postgresql+asyncpg://'
if 'HW1_TEST_IDENTITY_ENABLED' not in text:
    text = text.replace(anchor, '      HW1_TEST_IDENTITY_ENABLED: "true"\n'
                        '      HW1_TEST_IDENTITY_FILE: /run/hoistra-hw1/test-identities.json\n' + anchor)
    anchor = '    ports:\n      - "127.0.0.1:18008:8008"'
    assert anchor in text
    text = text.replace(anchor, '    volumes:\n'
                        '      - ./runtime/test-identities.json:/run/hoistra-hw1/test-identities.json:ro\n' + anchor)
    compose.write_text(text)
print('Private test sessions configured; isolated Compose identity mode enabled.')
