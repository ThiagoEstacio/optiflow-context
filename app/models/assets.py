"""
OptiFlow Context — Asset Models

Fonte da verdade do modelo de ativos da plataforma.
Todos os módulos (Vision, OPERA, Historian) consultam aqui.
Nenhum outro módulo deve definir seus próprios modelos de ativo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enumerações ───────────────────────────────────────────────────────────────

class HierarchyLevel(str, Enum):
    ENTERPRISE = "enterprise"   # nível topo: empresa / operadora
    SITE       = "site"         # planta / unidade operacional
    AREA       = "area"         # área de processo (ex: Sistema Carmo)
    UNIT       = "unit"         # unidade de processo (ex: Zona de Pressão Alta)
    EQUIPMENT  = "equipment"    # equipamento individual (VRP, bomba, reservatório)
    TAG        = "tag"          # ponto de medição (folha da hierarquia)


class AssetType(str, Enum):
    VRP          = "vrp"
    RESERVOIR    = "reservoir"
    PUMP_STATION = "pump_station"
    PIPELINE     = "pipeline"
    SENSOR       = "sensor"
    VALVE        = "valve"
    CONTROLLER   = "controller"
    PID_LOOP     = "pid_loop"
    ENERGY_METER = "energy_meter"
    ZONE         = "zone"
    GENERIC      = "generic"
    UNKNOWN      = "unknown"


class RelationType(str, Enum):
    PARENT       = "parent"       # hierarquia ISA-95
    FEEDS        = "feeds"        # supply chain hidráulico
    UPSTREAM     = "upstream"
    DOWNSTREAM   = "downstream"
    SAME_ZONE    = "same_zone"
    SAME_PROCESS = "same_process"
    CONTROLLED_BY = "controlled_by"
    CORRELATED   = "correlated"


class SignalVariable(str, Enum):
    PRESSURE_UPSTREAM   = "pressure_upstream"
    PRESSURE_DOWNSTREAM = "pressure_downstream"
    FLOW                = "flow"
    VALVE_POSITION      = "valve_position"
    SETPOINT            = "setpoint"
    OPERATING_MODE      = "operating_mode"
    LEVEL               = "level"
    VOLUME              = "volume"
    PUMP_SPEED          = "pump_speed"
    PUMP_STATUS         = "pump_status"
    TEMPERATURE         = "temperature"
    ENERGY              = "energy"
    STATUS              = "status"
    UNKNOWN             = "unknown"


# ── Modelos de API ─────────────────────────────────────────────────────────────

class AssetBase(BaseModel):
    name:       str
    level:      HierarchyLevel
    asset_type: AssetType     = AssetType.GENERIC
    parent_id:  str | None    = None
    device_id:  str | None    = None   # link para DeviceRegistry (só EQUIPMENT)
    tag_id:     str | None    = None   # link para tag (só TAG)
    site_id:    str | None    = None   # código do site (ex: "BR/SP/SAO")
    metadata:   dict[str, Any] = Field(default_factory=dict)


class AssetCreate(AssetBase):
    asset_id: str = Field(default_factory=lambda: str(uuid4()))


class Asset(AssetBase):
    asset_id:   str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssetTree(Asset):
    children: list["AssetTree"] = Field(default_factory=list)
    relations: list["AssetRelation"] = Field(default_factory=list)


class AssetRelationBase(BaseModel):
    source_id:     str
    target_id:     str
    relation_type: RelationType
    confidence:    float           = 1.0
    metadata:      dict[str, Any]  = Field(default_factory=dict)


class AssetRelationCreate(AssetRelationBase):
    pass


class AssetRelation(AssetRelationBase):
    id:         int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Modelos de classificação semântica ─────────────────────────────────────────

class SemanticClassification(BaseModel):
    tag:             str
    asset_type:      AssetType
    variable:        SignalVariable
    zone:            str | None  = None
    criticality:     str         = "medium"
    confidence:      float       = 0.0
    pattern_matched: str | None  = None
    asset_id_hint:   str | None  = None
    unit:            str | None  = None


# ── Discovery ─────────────────────────────────────────────────────────────────

class DiscoveryResult(BaseModel):
    run_id:             str = Field(default_factory=lambda: str(uuid4()))
    started_at:         datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at:       datetime | None = None
    assets_discovered:  int = 0
    relations_inferred: int = 0
    tags_classified:    int = 0
    sources:            list[str] = Field(default_factory=list)
    errors:             list[str] = Field(default_factory=list)
