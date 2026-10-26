// tabNotice — the page notice, and the tabs it must not hide. The service refuses the conversation, run and
// model-call reads of a platform superadmin who has named no company ("name_a_company"), and the page then says so in
// place of every tab. Progress, Labels, Judges and Watch are answered platform-wide (the platform team owns failure modes,
// builds the judges for them and runs monitoring across companies), so for those four that one refusal is not shown: the
// tab reads its own figures and says what is wrong with them itself.
// Any other refusal (not an admin, not your company) still stops the whole page. Pure: no requests, no state.
import { pageNotice } from './views.js';
import { PAGE_REASONS } from './shell.js';

export const PLATFORM_TABS = ['progress', 'labels', 'judges', 'watch'];

export function shellNotice(s) {
  if (PLATFORM_TABS.indexOf(s.tab) > -1) {
    const others = [s.convs, s.runs, s.calls].some((part) => part && PAGE_REASONS.indexOf(part.reason) > -1 && part.reason !== 'name_a_company');
    if (!others) return '';
  }
  return pageNotice(s);
}
