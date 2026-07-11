export interface ColumnMeta {
  name: string;
  type: string;
  nullable: boolean;
  is_primary_key: boolean;
  is_foreign_key: boolean;
  fk_table?: string | null;
  fk_column?: string | null;
}

export interface ColumnMetaListResult {
  datasource_id: string;
  datasource_name: string;
  engine: string;
  schema_name: string;
  table_name: string;
  columns: ColumnMeta[];
}

export interface ColumnAnnotationItem {
  column_name: string;
  annotation: string | null;
}

export interface TableAnnotationPutPayload {
  description?: string | null;
  annotations: ColumnAnnotationItem[];
}

export interface TableAnnotationResult {
  datasource_id: string;
  schema_name: string;
  table_name: string;
  description: string | null;
  column_annotations: ColumnAnnotationItem[];
}

export type RelationshipType = 'many-to-one' | 'one-to-one' | 'many-to-many';

export interface Relationship {
  id: string;
  datasource_id: string;
  schema_name: string;
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  relationship_type: RelationshipType;
  is_discovered: boolean;
  created_at: string;
  updated_at: string;
}

export interface RelationshipCreatePayload {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  relationship_type: RelationshipType;
}

export interface RelationshipListResult {
  datasource_id: string;
  schema_name: string;
  relationships: Relationship[];
  count: number;
}

/** Component-local merged row — not a backend type */
export interface ColumnRow extends ColumnMeta {
  annotation: string;
}
