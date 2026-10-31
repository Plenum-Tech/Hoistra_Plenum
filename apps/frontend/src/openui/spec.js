// spec — the components the orchestrator writes its answers with (OpenUI Lang), defined once.
//
// No React in here, on purpose. The chat builds its renderable library from these definitions
// (openui/library.jsx passes @openuidev/react-lang and the Hoistra renderers), and
// scripts/openui-prompt.mjs builds svc-deepagents' "Answer format" prompt section from the very
// same definitions (passing @openuidev/lang-core and no renderers). One source, so the model is
// never taught a component the chat cannot draw (test/openuiSpec.test.mjs holds the two equal).
//
// Props are positional in the order written here: that order IS the language's argument order,
// and svc-deepagents' Markdown rendition (agents/openui_view.py) maps arguments by it too.

export const ROOT = 'Answer';

// What an Answer may hold, in the order the prompt lists them.
export const BLOCKS = [
  'Headline', 'Text', 'Metrics', 'Table', 'BarChart', 'LineChart', 'Callout', 'Finding', 'Details',
  'Section', 'Steps', 'Buttons', 'FollowUps', 'EmailDraft'
];

export function makeLibrary(api, z, renderers = {}) {
  const { defineComponent, createLibrary, action } = api;
  const def = (name, description, props) =>
    defineComponent({ name, description, props: z.object(props), component: renderers[name] || null });

  const tone4 = z.enum(['risk', 'warn', 'ok', 'neutral']);
  const cell = z.union([z.string(), z.number(), z.boolean(), z.null()]);
  // Items of paired lists (labels with values, columns with cells) take either type: OpenUI
  // removes an item of the wrong type from its list, which would slide every later figure under
  // the label before it. A value a renderer cannot read is drawn as missing, in its own place.
  const label = z.union([z.string(), z.number()]);
  const figure = z.union([z.number(), z.string()]);

  const Headline = def('Headline',
    'The direct answer in one or two sentences. Always the first child of Answer. Put the key figures in **bold**.',
    { text: z.string() });
  const Text = def('Text',
    'Prose in Markdown: short paragraphs, bullet or numbered lists, **bold**, *italic* and links. Write \\n between lines.',
    { markdown: z.string() });
  const Metric = def('Metric',
    'One headline figure. value reads as it should be shown ("7", "91%", "£12,400"); note is a short comparison or context ("+3 on last week"); tone is risk for something bad or overdue, warn for something close to a limit, ok for something healthy, neutral otherwise.',
    { label: z.string(), value: z.union([z.string(), z.number()]), note: z.string().optional(), tone: tone4.optional() });
  const Metrics = def('Metrics', 'A row of 2 to 6 headline figures.',
    { items: z.array(Metric.ref) });
  const Table = def('Table',
    'Records with several fields. rows[i][j] is the cell under columns[j]. The chat shows the first rows and lets the reader open the rest.',
    { columns: z.array(label), rows: z.array(z.array(cell)), caption: z.string().optional() });
  const BarChart = def('BarChart',
    'Compares one measure across items such as trades, buildings or vendors. values[i] belongs to labels[i]; write null where there is no figure, never 0. unit is shown after each value ("kWh", "%", "days").',
    { title: z.string(), labels: z.array(label), values: z.array(figure), unit: z.string().optional() });
  const Series = def('Series', 'One line of a LineChart: a name and one value per period, null where there is none.',
    { name: z.string(), values: z.array(figure) });
  const LineChart = def('LineChart',
    'A measure over time. labels are the periods in order; every Series has one value per label.',
    { title: z.string(), labels: z.array(label), series: z.array(Series.ref), unit: z.string().optional() });
  const Callout = def('Callout',
    'One finding that needs attention, set apart from the rest. tone is risk, warn, ok or info.',
    { tone: z.enum(['risk', 'warn', 'ok', 'info']), text: z.string(), title: z.string().optional() });
  // 28 Oct 2026: an answer listing several issues (often bold-lead paragraphs) read as one slab of
  // prose. Each issue may now be a Finding; consecutive Findings stack as a list in the chat.
  const Finding = def('Finding',
    'One issue in a list of issues, each about one asset, certificate, vendor or work order. title is the issue\'s lead and text the rest of it in Markdown, both exactly as written; code is its record or asset code exactly as written, only when there is one; tone is risk for something lapsed, expired, overdue, blocked or failed, warn for something due soon or expiring, ok for something in date or healthy, neutral otherwise.',
    { title: z.string(), text: z.string(), tone: tone4.optional(), code: z.string().optional() });
  const Field = def('Field', 'One label and its value inside Details.',
    { label: z.string(), value: z.union([z.string(), z.number()]) });
  const Details = def('Details',
    'The fields of one record: an asset, a work order, a vendor or a certificate.',
    { title: z.string(), fields: z.array(Field.ref), subtitle: z.string().optional() });
  const Button = def('Button',
    'Asks the assistant with @ToAssistant("question") or opens a page with @OpenUrl("/compliance"). Without an action it asks its own label.',
    { label: z.string(), action: action().optional(), variant: z.enum(['primary', 'secondary']).optional() });
  const Buttons = def('Buttons', 'A row of buttons.', { items: z.array(Button.ref) });
  const Step = def('Step', 'One thing to do, with an optional Button that does it or asks for it.',
    { title: z.string(), detail: z.string().optional(), action: Button.ref.optional() });
  const Steps = def('Steps', 'What to do next, in order.', { items: z.array(Step.ref) });
  const FollowUps = def('FollowUps',
    'Two or three short questions the user is likely to ask next. Clicking one asks it.',
    { questions: z.array(z.string()) });
  const EmailDraft = def('EmailDraft',
    'An email for the user to send: subject, recipients, and the body as plain text with \\n between lines.',
    { subject: z.string(), to: z.string(), body: z.string(), cc: z.string().optional() });

  const leaf = [Headline, Text, Metrics, Table, BarChart, LineChart, Callout, Finding, Details];
  const tail = [Steps, Buttons, FollowUps, EmailDraft];
  const Section = def('Section',
    'A titled group of blocks. Set collapsed to true for supporting detail the reader may skip; summary is the one line shown while it is closed.',
    { title: z.string(), children: z.array(z.union([...leaf, ...tail].map((c) => c.ref))), summary: z.string().optional(), collapsed: z.boolean().optional() });
  const Answer = def('Answer', 'The whole answer. Always the root; its children are shown top to bottom.',
    { children: z.array(z.union([...leaf, Section, ...tail].map((c) => c.ref))) });

  return createLibrary({
    root: ROOT,
    components: [Answer, Headline, Text, Metrics, Metric, Table, BarChart, LineChart, Series, Callout,
      Finding, Details, Field, Section, Steps, Step, Buttons, Button, FollowUps, EmailDraft],
    componentGroups: [
      { name: 'Layout', components: ['Answer', 'Section'] },
      { name: 'Text', components: ['Headline', 'Text', 'Callout', 'Finding'] },
      { name: 'Figures and records', components: ['Metrics', 'Metric', 'Table', 'BarChart', 'LineChart', 'Series', 'Details', 'Field'] },
      { name: 'Next steps', components: ['Steps', 'Step', 'Buttons', 'Button', 'FollowUps', 'EmailDraft'] }
    ]
  });
}

// The worked examples the prompt shows. They use made-up buildings and vendors so the model
// cannot mistake them for the portfolio it is answering about; the test parses each one.
export const EXAMPLES = [
  [
    'root = Answer([head, kpis, certs, trades, overdue, todo, next])',
    'head = Headline("Three things need you this week: the gas safety certificate at Building A expires on **Friday**, **7 work orders** are overdue and Building A is using **18% more energy** than in early October.")',
    'kpis = Metrics([m1, m2, m3, m4])',
    'm1 = Metric("Overdue work orders", 7, "+3 on last week", "risk")',
    'm2 = Metric("Certificates due in 30 days", 4, "1 this week", "warn")',
    'm3 = Metric("SLA met this month", "91%", "-2 pt on September")',
    'm4 = Metric("Building A energy use", "241.0 kWh/m²", "Building B: 178.4")',
    'certs = Section("Certificates to act on", [f1, f2])',
    'f1 = Finding("Gas safety certificate, Building A", "Expires on Friday and the inspection is not booked yet.", "warn", "GSC-A-2025")',
    'f2 = Finding("Lift thorough examination, Building B", "Expires on 9 Nov; Liftcare has the visit booked for 4 Nov.", "warn")',
    'trades = BarChart("Overdue work orders by trade", ["HVAC", "Electrical", "Plumbing", "Fire safety"], [3, 2, 1, 1])',
    'overdue = Section("Overdue work orders", [wo], "7 past their due date, 3 of them HVAC")',
    'wo = Table(["Work order", "Job", "Building", "Days overdue", "Vendor"], [["WO-1184", "AHU-2 drive belt replacement", "Building A", 9, "Acme Mechanical"], ["WO-1179", "Emergency lighting monthly test", "Building B", 6, "Brightline Electrical"], ["WO-1171", "Chiller 1 high-pressure alarm", "Building A", 5, "Acme Mechanical"]], "4 more: 2 plumbing, 1 electrical, 1 fire safety")',
    'todo = Section("What to do", [steps])',
    'steps = Steps([s1, s2, s3])',
    's1 = Step("Book the gas safety inspection at Building A", "The certificate expires on Friday.", b1)',
    'b1 = Button("Draft the booking email", @ToAssistant("Draft an email to book the gas safety inspection at Building A"), "primary")',
    's2 = Step("Chase Acme Mechanical on the 3 HVAC jobs", "Two are past their SLA.", b2)',
    'b2 = Button("Show the HVAC backlog", @ToAssistant("Show the HVAC backlog at Building A"))',
    's3 = Step("Check the AHU-2 schedule at Building A", "It has run overnight since 12 Oct.")',
    'next = FollowUps(["Compare energy use at Building A and Building B", "Which vendors are slipping on SLA?"])'
  ].join('\n'),
  [
    'root = Answer([ask, choices])',
    'ask = Headline("Which building do you mean? **Building A** and **Building B** both have a chiller.")',
    'choices = FollowUps(["The chiller at Building A", "The chiller at Building B", "Both chillers"])'
  ].join('\n')
];

export const promptOptions = {
  preamble: 'Write your final answer to the user as an OpenUI Lang program built from the components below.',
  examples: EXAMPLES,
  additionalRules: [
    'Your final answer is the program and nothing else: no prose before or after it, and no code fences.',
    'Write root = Answer([...]) first, then define its children in reading order, top to bottom, so the answer appears in order while it streams.',
    'The first child of Answer is a Headline that answers the question directly. Put the key figures in **bold**.',
    'Every figure, date, name and code you show came from your tools in this conversation. Never estimate or invent one; leave out what you do not have.',
    'Strings use double quotes. Write a line break as \\n inside a string, never as a real line break, and never put three backticks in a string.',
    'Choose by content: Metrics for 2 to 6 headline figures; Table for records with several fields; BarChart to compare one measure across items; LineChart for a measure over time; Details for the fields of one record; Callout for one finding that needs attention; Steps for what to do next; EmailDraft for each email to send.',
    'Use Finding when the answer lists several issues, each about one asset, certificate, vendor or work order (in prose often a bold lead, then the detail): one Finding per issue, in the order you would list them, one after another. Keep Callout for a single caveat set apart from the rest, and Text for prose that is not a list of issues.',
    'Copy a Finding\'s title, text and code exactly as you would write them in prose; nothing is added, dropped or reworded. Give code only when the answer names a record or asset code from your tools; never make one up. When the lead names the issue\'s code in brackets, leave the brackets out of the title and give that code as code, so the code is shown once.',
    'A Finding\'s tone: risk only for something lapsed, expired, overdue, blocked or failed; warn for something due soon or expiring; ok for something in date or healthy; neutral for anything else. Its own words must say it: the chat shows a tone only when the title or text carries it.',
    'Include every row the question asks for in a Table; the chat shows the first rows and the reader opens the rest. A count you state must match the rows you list.',
    'Put the whole question in the message of @ToAssistant: it is asked exactly as written, and nothing else is passed with it.',
    'Put supporting detail the reader may skip in a Section with collapsed set to true and a one-line summary.',
    'Cite a document inside Text as a Markdown link: [file name](doc:<id>). Never show a raw ID.',
    'To send the user to a page of the app, use a Button with @OpenUrl and the page path: /compliance, /energy, /vendors, /assets or /maintenance. For example Button("Open Compliance", @OpenUrl("/compliance")).',
    'End with FollowUps of two or three short questions the user is likely to ask next, when there is a natural next step.',
    'When you must ask the user something before you can answer, write a Headline with the question and FollowUps with the likely answers.',
    'When a tool says the interface shows its results as cards, answer with a Headline (and FollowUps) and do not repeat what the cards show.',
    'Say each figure once. The components carry the structure, so do not repeat a figure in several places.',
    'Messages you write before or between tool calls are short plain sentences, never programs.'
  ]
};
