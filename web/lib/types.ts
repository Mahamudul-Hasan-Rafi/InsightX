export interface NavItem {
  id: string;
  label: string;
  icon: string;
  /**
   * The feature segment from Keycloak roles: `feat:<feature>:view`.
   * Omit to show the item to all authenticated users.
   */
  feature?: string;
}

export interface HistoryItem {
  id: string;
  title: string;
}

export interface HistoryGroup {
  day: string;
  items: HistoryItem[];
}

export interface ChartPoint {
  label?: string;
  value: number;
}

export interface Suggestion {
  icon: string;
  text: string;
}

export interface InsightTable {
  cols: string[];
  rows: string[][];
}

export interface InsightMeta {
  rows: number;
  ms: number;
  tokens: number;
}

export interface InsightData {
  narrative: string;
  chartType?: 'bar' | 'line';
  chartTitle?: string;
  chart?: ChartPoint[];
  table?: InsightTable;
  sql?: string;
  followups?: string[];
  meta?: InsightMeta;
}

export type Message =
  | { role: 'user'; text: string }
  | { role: 'assistant'; insight: InsightData };

export interface Conversation {
  id: string;
  source: string;
  messages: Message[];
}

export interface DbType {
  id: string;
  name: string;
  tag: string;
  port: string;
  blurb: string;
  color: string;
  letter: string;
}

export interface ConnectedSource {
  id: string;
  name: string;
  type: string;
  letter: string;
  slug: string;
  color: string;
  host: string;
  status: 'connected' | 'syncing' | 'error';
  tables: number;
  lastSync: string;
}

export interface TableInfo {
  name: string;
  rows: string;
  cols: number;
  annotated: number;
  desc: string;
}

export interface TableColumn {
  name: string;
  type: string;
  key: '' | 'PK' | 'FK';
  note: string;
  annotated: boolean;
}

export interface Relationship {
  from: string;
  to: string;
  type: string;
}

export interface User {
  name: string;
  email: string;
  role: 'Administrator' | 'Analyst' | 'Branch Officer' | 'Viewer';
  team: string;
  status: 'active' | 'invited' | 'suspended';
  last: string;
  initials: string;
  tint: string;
}

export type RolePerms = Record<string, string[]>;

export interface GlossaryItem {
  term: string;
  full: string;
  cat: string;
  behavior: string;
}

export interface FrequentQuery {
  text: string;
  runs: number;
  icon: string;
}

export interface TokenUsagePoint {
  d: string;
  v: number;
}

export interface DashStat {
  label: string;
  value: string;
  delta: string;
  up: boolean;
  icon: string;
}

export interface ApiKey {
  name: string;
  prefix: string;
  tail: string;
  created: string;
  last: string;
  scope: string;
  status: 'active' | 'revoked';
}

export interface ApiEndpoint {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE';
  path: string;
  desc: string;
}

export interface ModelOption {
  id: string;
  name: string;
  desc: string;
  tag: string;
  hosted: 'On-prem' | 'Cloud';
}

export interface AppNotification {
  icon: string;
  tint: 'blue' | 'amber' | 'green' | 'purple';
  title: string;
  body: string;
  time: string;
  unread: boolean;
}
