// taxonomyVals — every view model the taxonomy screens read, as one object: the failure-mode drawer, dialogs, map,
// notes, requirements and Progress rows (taxonomy.js), the Labels grid and its cell panel (labels.js), the AI
// suggestion queue and proposal runs (proposals.js) and the suggestions on the open answer (suggest.js). `s` is the
// store state; `a` is taxonomyActions.js (`import * as a`), which the buttons these models carry call by name.
import { taxonomyModesVals } from './taxonomy.js';
import { taxonomyLabelsVals } from './labels.js';
import { taxonomyProposalsVals } from './proposals.js';
import { taxonomySuggestVals } from './suggest.js';

export function taxonomyVals(s, a) {
  return Object.assign({}, taxonomyModesVals(s, a), taxonomyLabelsVals(s, a), taxonomyProposalsVals(s, a), taxonomySuggestVals(s, a));
}
