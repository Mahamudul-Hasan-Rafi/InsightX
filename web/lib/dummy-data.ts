import type {
  NavItem, HistoryGroup, Conversation, Suggestion,
  DbType, ConnectedSource, TableInfo, TableColumn, Relationship,
  User, RolePerms, GlossaryItem, FrequentQuery, TokenUsagePoint,
  DashStat, ApiKey, ApiEndpoint, ModelOption, AppNotification,
} from './types';

// feature must match the middle segment of a Keycloak role: feat:<feature>:view
export const NAV: NavItem[] = [
  { id: 'insight',    label: 'Insight',     icon: 'insight',    feature: 'insight' },
  { id: 'dashboard',  label: 'Dashboard',   icon: 'dashboard',  feature: 'dashboard' },
  { id: 'datasource', label: 'Data Source', icon: 'database',   feature: 'datasource' },
  { id: 'users',      label: 'Users',       icon: 'users',      feature: 'users' },
  { id: 'glossary',   label: 'Glossary',    icon: 'glossary',   feature: 'glossary' },
  { id: 'developers', label: 'Developers',  icon: 'developers', feature: 'developer' },
];

export const HISTORY: HistoryGroup[] = [
  { day: 'Today', items: [
    { id: 'c1', title: 'Top branches by deposit growth Q1' },
    { id: 'c2', title: 'NPL ratio trend vs last year' },
    { id: 'c3', title: 'High-value customers at churn risk' },
  ]},
  { day: 'Yesterday', items: [
    { id: 'c4', title: 'Average loan approval time by product' },
    { id: 'c5', title: 'Dormant accounts over 90 days' },
  ]},
  { day: 'Previous 7 days', items: [
    { id: 'c6', title: 'Card spend by merchant category' },
    { id: 'c7', title: 'CASA balance mix by region' },
    { id: 'c8', title: 'Failed transactions after 6pm' },
    { id: 'c9', title: 'Loan portfolio exposure by sector' },
  ]},
];

export const SAMPLE_CHAT: Conversation = {
  id: 'c1',
  source: 'Core Banking · Oracle',
  messages: [
    { role: 'user', text: 'Which branches had the strongest deposit growth in Q1 2026?' },
    { role: 'assistant', insight: {
      narrative: "Deposit growth concentrated in the metro corridor this quarter. The **Gulshan** branch led with **+18.4%** quarter-over-quarter growth in total deposits, driven mainly by term deposits from corporate clients. The top five branches together added **৳4.21B** in new deposits — about 63% of the bank's net deposit gain for Q1.",
      chartType: 'bar',
      chartTitle: 'Deposit growth by branch — Q1 2026 (% QoQ)',
      chart: [
        { label: 'Gulshan',    value: 18.4 },
        { label: 'Banani',     value: 14.1 },
        { label: 'Dhanmondi',  value: 11.7 },
        { label: 'Uttara',     value: 9.3 },
        { label: 'Motijheel',  value: 7.8 },
      ],
      table: {
        cols: ['Branch', 'New deposits', 'QoQ growth', 'Top driver'],
        rows: [
          ['Gulshan',   '৳1.34B', '+18.4%', 'Corporate TD'],
          ['Banani',    '৳0.98B', '+14.1%', 'Retail CASA'],
          ['Dhanmondi', '৳0.81B', '+11.7%', 'Term deposit'],
          ['Uttara',    '৳0.61B', '+9.3%',  'Payroll inflow'],
          ['Motijheel', '৳0.47B', '+7.8%',  'SME current'],
        ],
      },
      sql: "SELECT b.branch_name,\n       SUM(d.balance) - SUM(d.prev_balance)      AS new_deposits,\n       ROUND((SUM(d.balance) / NULLIF(SUM(d.prev_balance),0) - 1)*100, 1) AS qoq_growth\nFROM   deposits d\nJOIN   branches b ON b.branch_id = d.branch_id\nWHERE  d.period = '2026-Q1'\nGROUP  BY b.branch_name\nORDER  BY qoq_growth DESC\nLIMIT  5;",
      followups: [
        'Break Gulshan down by deposit product',
        'Compare against Q1 2025',
        'Which corporate clients drove the term-deposit inflow?',
      ],
      meta: { rows: 5, ms: 1840, tokens: 2140 },
    }},
  ],
};

export const CONVERSATIONS: Record<string, Conversation> = {
  c1: SAMPLE_CHAT,
};

export const SUGGESTIONS: Suggestion[] = [
  { icon: 'chart', text: 'Show deposit growth by branch for last quarter' },
  { icon: 'line',  text: 'Plot the NPL ratio trend over the last 12 months' },
  { icon: 'users', text: 'List high-value customers likely to churn' },
  { icon: 'coins', text: 'Break down card spend by merchant category' },
];

export const DB_TYPES: DbType[] = [
  { id: 'oracle',    name: 'Oracle DB',    tag: 'Enterprise',   port: '1521',
    blurb: 'Connect to Oracle 12c–21c core banking instances.',
    color: 'oklch(0.6 0.17 28)',   letter: 'O' },
  { id: 'postgres',  name: 'PostgreSQL',   tag: 'Open source',  port: '5432',
    blurb: 'Connect to PostgreSQL 12+ analytics warehouses.',
    color: 'oklch(0.55 0.13 250)', letter: 'P' },
  { id: 'sqlserver', name: 'SQL Server',   tag: 'Microsoft',    port: '1433',
    blurb: 'Connect to Microsoft SQL Server 2016+ databases.',
    color: 'oklch(0.58 0.15 18)',  letter: 'S' },
  { id: 'delta',     name: 'Delta Lakehouse', tag: 'Spark/HDFS', port: '7077',
    blurb: 'Connect to Delta Lake tables on a Spark/HDFS cluster.',
    color: 'oklch(0.62 0.14 200)', letter: 'D' },
];

export const CONNECTED: ConnectedSource[] = [
  { id: 'core',      name: 'Core Banking',         type: 'Oracle DB',   letter: 'O', slug: 'oracle',
    color: 'oklch(0.6 0.17 28)',
    host: 'orcl-core.bank.internal:1521/COREPDB',
    status: 'connected', tables: 24, lastSync: '8 min ago' },
  { id: 'warehouse', name: 'Analytics Warehouse',  type: 'PostgreSQL',  letter: 'P', slug: 'postgres',
    color: 'oklch(0.55 0.13 250)',
    host: 'pg-warehouse.bank.internal:5432/analytics',
    status: 'connected', tables: 61, lastSync: '1 hr ago' },
  { id: 'cards',     name: 'Cards & Payments',      type: 'SQL Server',  letter: 'S', slug: 'sqlserver',
    color: 'oklch(0.58 0.15 18)',
    host: 'mssql-cards.bank.internal:1433/CARDS',
    status: 'syncing',   tables: 18, lastSync: 'syncing…' },
];

export const TABLES: TableInfo[] = [
  { name: 'CUSTOMERS',    rows: '2.41M', cols: 18, annotated: 18, desc: 'Master record for all retail & corporate customers' },
  { name: 'ACCOUNTS',     rows: '3.18M', cols: 22, annotated: 20, desc: 'Deposit, current and savings accounts' },
  { name: 'TRANSACTIONS', rows: '481M',  cols: 14, annotated: 9,  desc: 'All posted account transactions' },
  { name: 'LOANS',        rows: '186K',  cols: 26, annotated: 26, desc: 'Loan accounts across all products' },
  { name: 'DEPOSITS',     rows: '892K',  cols: 16, annotated: 12, desc: 'Term & fixed deposit positions' },
  { name: 'BRANCHES',     rows: '214',   cols: 11, annotated: 11, desc: 'Branch master with region & geo data' },
  { name: 'CARDS',        rows: '1.04M', cols: 19, annotated: 7,  desc: 'Debit & credit card master' },
  { name: 'COLLATERAL',   rows: '142K',  cols: 13, annotated: 0,  desc: 'Loan collateral & valuation records' },
];

export const TABLE_COLUMNS: TableColumn[] = [
  { name: 'account_id',   type: 'NUMBER(18)',    key: 'PK', note: 'Unique account identifier',              annotated: true },
  { name: 'customer_id',  type: 'NUMBER(18)',    key: 'FK', note: 'References CUSTOMERS.customer_id',       annotated: true },
  { name: 'branch_id',    type: 'NUMBER(8)',     key: 'FK', note: 'Owning branch — references BRANCHES',    annotated: true },
  { name: 'account_type', type: 'VARCHAR(12)',   key: '',   note: 'CASA / TD / LOAN / OD',                  annotated: true },
  { name: 'balance',      type: 'NUMBER(18,2)',  key: '',   note: 'Current ledger balance in BDT',          annotated: true },
  { name: 'prev_balance', type: 'NUMBER(18,2)',  key: '',   note: 'Closing balance of prior period',        annotated: true },
  { name: 'status',       type: 'VARCHAR(10)',   key: '',   note: 'ACTIVE / DORMANT / CLOSED',              annotated: true },
  { name: 'opened_at',    type: 'DATE',          key: '',   note: 'Account opening date',                   annotated: false },
  { name: 'currency',     type: 'CHAR(3)',        key: '',   note: '',                                       annotated: false },
];

export const RELATIONSHIPS: Relationship[] = [
  { from: 'ACCOUNTS.customer_id',     to: 'CUSTOMERS.customer_id',  type: 'Many-to-one' },
  { from: 'ACCOUNTS.branch_id',       to: 'BRANCHES.branch_id',     type: 'Many-to-one' },
  { from: 'TRANSACTIONS.account_id',  to: 'ACCOUNTS.account_id',    type: 'Many-to-one' },
];

export const USERS: User[] = [
  { name: 'Farhana Rahman',   email: 'f.rahman@bank.com',     role: 'Administrator',  team: 'Data & Analytics',    status: 'active',    last: '2 min ago',   initials: 'FR', tint: 'oklch(0.6 0.15 28)' },
  { name: 'Tanvir Ahmed',     email: 't.ahmed@bank.com',      role: 'Analyst',        team: 'Retail Banking',      status: 'active',    last: '1 hr ago',    initials: 'TA', tint: 'oklch(0.58 0.14 250)' },
  { name: 'Nusrat Jahan',     email: 'n.jahan@bank.com',      role: 'Branch Officer', team: 'Gulshan Branch',      status: 'active',    last: 'Today',       initials: 'NJ', tint: 'oklch(0.58 0.15 300)' },
  { name: 'Imran Hossain',    email: 'i.hossain@bank.com',    role: 'Analyst',        team: 'Risk & Compliance',   status: 'active',    last: 'Yesterday',   initials: 'IH', tint: 'oklch(0.6 0.13 158)' },
  { name: 'Sadia Karim',      email: 's.karim@bank.com',      role: 'Branch Officer', team: 'Banani Branch',       status: 'invited',   last: '—',           initials: 'SK', tint: 'oklch(0.62 0.13 75)' },
  { name: 'Rakib Chowdhury',  email: 'r.chowdhury@bank.com',  role: 'Viewer',         team: 'Treasury',            status: 'active',    last: '3 days ago',  initials: 'RC', tint: 'oklch(0.55 0.13 200)' },
  { name: 'Mehjabin Alam',    email: 'm.alam@bank.com',        role: 'Branch Officer', team: 'Dhanmondi Branch',   status: 'suspended', last: '2 weeks ago', initials: 'MA', tint: 'oklch(0.58 0.16 350)' },
];

export const ROLE_PERMS: RolePerms = {
  'Administrator':  ['Full access', 'Manage users', 'Manage data sources', 'Billing'],
  'Analyst':        ['Run insights', 'View all sources', 'Export results'],
  'Branch Officer': ['Run insights', 'Own branch data', 'View dashboards'],
  'Viewer':         ['View dashboards', 'View saved insights'],
};

export const GLOSSARY: GlossaryItem[] = [
  { term: 'NPL',              full: 'Non-Performing Loan',              cat: 'Risk',
    behavior: 'Treat as loans with payments overdue 90+ days. Compute NPL ratio as NPL balance ÷ gross loan portfolio, expressed as a percentage.' },
  { term: 'CASA',             full: 'Current Account & Savings Account', cat: 'Deposits',
    behavior: 'Sum of all current and savings account balances. Use for low-cost deposit ratio analysis; exclude term deposits.' },
  { term: 'Dormant account',  full: '',                                  cat: 'Accounts',
    behavior: 'Account with no customer-initiated transaction for 90+ consecutive days and status = DORMANT.' },
  { term: 'High-value customer', full: '',                               cat: 'Segments',
    behavior: 'Customer with total relationship balance ≥ ৳5,000,000 across all accounts, or flagged as priority/private banking.' },
  { term: 'Churn risk',       full: '',                                  cat: 'Segments',
    behavior: 'Score customers by declining balance trend, falling transaction frequency, and product attrition over the trailing 6 months.' },
  { term: 'Deposit growth',   full: '',                                  cat: 'Deposits',
    behavior: 'Quarter-over-quarter change in total deposit balance. Always state the comparison period and report as % unless asked for absolute.' },
  { term: 'Exposure',         full: '',                                  cat: 'Risk',
    behavior: 'Outstanding principal plus undrawn committed facilities. Group by sector or counterparty when asked for concentration.' },
];

export const FREQUENT_QUERIES: FrequentQuery[] = [
  { text: 'Deposit growth by branch',              runs: 142, icon: 'chart' },
  { text: 'NPL ratio by product',                  runs: 118, icon: 'shield' },
  { text: 'Dormant accounts over 90 days',         runs: 96,  icon: 'clock' },
  { text: 'Top customers by relationship value',   runs: 73,  icon: 'star' },
  { text: 'Card spend by merchant category',       runs: 64,  icon: 'coins' },
];

export const TOKEN_USAGE: TokenUsagePoint[] = [
  { d: 'Mon', v: 142 }, { d: 'Tue', v: 198 }, { d: 'Wed', v: 167 },
  { d: 'Thu', v: 254 }, { d: 'Fri', v: 312 }, { d: 'Sat', v: 88 }, { d: 'Sun', v: 61 },
];

export const DASH_STATS: DashStat[] = [
  { label: 'Insights generated', value: '1,284', delta: '+12.4%',   up: true,  icon: 'sparkle' },
  { label: 'Tokens used (mo.)',   value: '4.82M', delta: '+8.1%',    up: true,  icon: 'coins' },
  { label: 'Avg. response time',  value: '1.9s',  delta: '−0.3s',   up: true,  icon: 'bolt' },
  { label: 'Active data sources', value: '3',     delta: 'all healthy', up: true, icon: 'database' },
];

export const API_KEYS: ApiKey[] = [
  { name: 'Production',        prefix: 'ix_live_', tail: '8Kd2', created: 'Jan 12, 2026', last: '4 min ago',  scope: 'Read · Write', status: 'active' },
  { name: 'Reporting service', prefix: 'ix_live_', tail: 'q7Lm', created: 'Feb 28, 2026', last: '2 hr ago',   scope: 'Read only',    status: 'active' },
  { name: 'Staging',           prefix: 'ix_test_', tail: 'vR9p', created: 'Mar 04, 2026', last: '6 days ago', scope: 'Read · Write', status: 'active' },
];

export const API_ENDPOINTS: ApiEndpoint[] = [
  { method: 'POST', path: '/v1/insights',                    desc: 'Generate an insight from a natural-language question.' },
  { method: 'GET',  path: '/v1/insights/{id}',               desc: 'Retrieve a previously generated insight and its result set.' },
  { method: 'GET',  path: '/v1/sources',                     desc: 'List connected data sources and their sync status.' },
  { method: 'GET',  path: '/v1/sources/{id}/tables',         desc: 'List tables and annotation status for a source.' },
  { method: 'POST', path: '/v1/glossary',                    desc: 'Create or update a glossary keyword definition.' },
];

export const MODELS: ModelOption[] = [
  { id: 'ix-analyst-3',    name: 'InsightX Analyst 3',    desc: 'Best for complex multi-table banking analysis',    tag: 'Recommended', hosted: 'On-prem' },
  { id: 'ix-analyst-lite', name: 'InsightX Analyst Lite', desc: 'Faster, lower token cost for routine queries',     tag: 'Fast',        hosted: 'On-prem' },
  { id: 'gpt-4o',          name: 'GPT-4o',                desc: 'Cloud model — requires your provider credentials', tag: 'Cloud',       hosted: 'Cloud' },
  { id: 'claude-sonnet',   name: 'Claude Sonnet',         desc: 'Cloud model — strong reasoning, requires credentials', tag: 'Cloud',  hosted: 'Cloud' },
];

export const NOTIFICATIONS: AppNotification[] = [
  { icon: 'database', tint: 'blue',   title: 'Cards & Payments sync in progress',   body: '18 tables · started 4 minutes ago',          time: '4m', unread: true },
  { icon: 'shield',   tint: 'amber',  title: 'NPL ratio crossed 4.0% threshold',    body: 'Risk alert on Loan Portfolio dashboard',       time: '1h', unread: true },
  { icon: 'users',    tint: 'purple', title: 'Sadia Karim accepted her invite',      body: 'Branch Officer · Banani Branch',               time: '3h', unread: true },
  { icon: 'coins',    tint: 'green',  title: 'Monthly token usage at 62%',           body: '4.82M of 7.8M tokens used this cycle',         time: '1d', unread: false },
  { icon: 'key',      tint: 'blue',   title: 'New API key created',                  body: 'Staging · ix_test_…vR9p',                      time: '6d', unread: false },
];
