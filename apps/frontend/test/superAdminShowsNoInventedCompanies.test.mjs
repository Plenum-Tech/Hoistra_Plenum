// The Super Admin console never shows invented companies as real ones.
//
// It mounted holding four of them — "Plenum Technologies · 8,420 cr", "Meridian REIT ·
// 3,180 cr", "Gulf Estates FZ", "Lion City Properties" — and showed them for as long as
// the companies read took to answer. Nothing said they were samples: the "Showing sample
// data" note only appears once the read has FAILED. So on a healthy login a platform
// operator read fabricated credit consumption presented exactly as the real thing, and
// then watched it change to "Your Organisation · 245 cr" underneath them.
//
// The sample rows still exist for a console that cannot reach its backend. What changed is
// that they now only appear together with the sentence that says what they are.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { SA_COMPANIES } = await import('../src/data/hoistra-access.js');
const { superAdminMethods } = await import('../src/logic/superAdmin.js');

const INVENTED = SA_COMPANIES.map((c) => c.name);

function vals(state) {
  const ctx = { state, setState() {} };
  return superAdminMethods.saVals.call(ctx, state);
}

const FRESH = {
  saOn: true, saSel: null, saCompanies: [],
  saLiveLoading: true, saLiveError: '', saLiveLoadedAt: null,
  saLiveCardError: '', saNew: false, saName: '', saEmail: '', saCc: 'UK',
};

test('nothing invented is on screen before the read answers', () => {
  const v = vals(FRESH);
  assert.equal(v.saCompanies.length, 0, 'the console mounted holding sample companies');
  for (const name of INVENTED) {
    assert.ok(!JSON.stringify(v.saCompanies).includes(name), `${name} is on screen`);
  }
});

test('an unanswered read does not claim the platform is empty', () => {
  // "No companies yet" is a finding, and it is not one anybody has established yet.
  const v = vals(FRESH);
  const rendered = JSON.stringify(v);
  assert.ok(!rendered.includes('No companies yet'),
    'it asserted an empty platform while the read was still in flight');
  assert.ok(rendered.includes('Reading the platform'),
    'it should say the read is still running');
});

test('a genuinely empty platform still says so once the read has answered', () => {
  const v = vals({ ...FRESH, saLiveLoading: false, saLiveLoadedAt: '2026-09-23T10:00:00Z' });
  assert.ok(JSON.stringify(v).includes('No companies yet'));
});

test('sample rows never appear without the sentence that explains them', () => {
  // The only state that puts them on screen is a failed read, and that state also
  // produces the banner. If one is ever true without the other, this fails.
  const failed = {
    ...FRESH,
    saLiveLoading: false,
    saLiveError: 'Failed to fetch',
    saCompanies: SA_COMPANIES.map((c) => ({ ...c })),
    saSel: SA_COMPANIES[0].id,
  };
  const v = vals(failed);
  assert.ok(v.saCompanies.length > 0, 'the fallback still fills a blank console');
  assert.match(v.saLiveError, /Showing sample data/,
    'sample rows were on screen with nothing saying they were samples');
});

test('a console already showing real companies is never replaced by samples', () => {
  const real = [{ id: 'org-1', name: 'Northbridge Estates Ltd', cc: 'UK', status: 'Active',
                  credits: 0, live: true }];
  const v = vals({ ...FRESH, saLiveLoading: false, saLiveLoadedAt: '2026-09-23T10:00:00Z',
                   saCompanies: real, saSel: 'org-1' });
  assert.equal(v.saCompanies.length, 1);
  assert.equal(v.saCompanies[0].name, 'Northbridge Estates Ltd');
});
