// library — the orchestrator's OpenUI components with their Hoistra renderers.
//
// Built once, at module scope: OpenUI parses again from scratch whenever the library object
// changes, so a library made inside a component would re-parse every answer on every render.
// The definitions are spec.js's (the same ones svc-deepagents' prompt is generated from).
import * as reactLang from '@openuidev/react-lang';
import { array, boolean, enum as zEnum, null as zNull, number, object, string, union } from 'zod';
import { makeLibrary } from './spec.js';
import { Answer, Section } from './components/Layout.jsx';
import { Callout, Details, Field, Headline, Text } from './components/Content.jsx';
import { Finding } from './components/Finding.jsx';
import { Metric, Metrics, Table } from './components/Data.jsx';
import { BarChart, LineChart, Series } from './components/Charts.jsx';
import { Button, Buttons, EmailDraft, FollowUps, Step, Steps } from './components/Actions.jsx';
import './openui.css';

// Only the zod builders the spec uses. Handing over zod's whole `z` namespace kept every one of
// its members in the bundle, all 64 error-message locales included (about 70 KB gzipped).
const z = { array, boolean, enum: zEnum, null: zNull, number, object, string, union };

export const library = makeLibrary(reactLang, z, {
  Answer, Section, Headline, Text, Callout, Finding, Details, Field, Metrics, Metric, Table,
  BarChart, LineChart, Series, Steps, Step, Buttons, Button, FollowUps, EmailDraft
});
