# api/app/modules/annotations/schemas.py

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

RelationshipType = Literal["many-to-one", "one-to-one", "many-to-many"]


class ColumnAnnotationItem(BaseModel):
    column_name: str
    annotation:  Optional[str] = None


class TableAnnotationPutPayload(BaseModel):
    description: Optional[str] = None
    annotations: list[ColumnAnnotationItem]


class RelationshipCreatePayload(BaseModel):
    from_table:        str = Field(min_length=1)
    from_column:       str = Field(min_length=1)
    to_table:          str = Field(min_length=1)
    to_column:         str = Field(min_length=1)
    relationship_type: RelationshipType


class TableAnnotationResponse(BaseModel):
    datasource_id:      str
    schema_name:        str
    table_name:         str
    description:        Optional[str]
    column_annotations: list[ColumnAnnotationItem]


class RelationshipResponse(BaseModel):
    id:                str
    datasource_id:     str
    schema_name:       str
    from_table:        str
    from_column:       str
    to_table:          str
    to_column:         str
    relationship_type: str
    is_discovered:     bool
    created_at:        str
    updated_at:        str


class RelationshipListResponse(BaseModel):
    datasource_id: str
    schema_name:   str
    relationships: list[RelationshipResponse]
    count:         int
