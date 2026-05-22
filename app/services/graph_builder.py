"""
OptiFlow Context — Asset Graph Builder

Responsabilidade: construir o grafo de ativos a partir de uma lista de tags.
Migrado do OPERA (process_intelligence.asset_graph_builder) para o Context.

Nenhum outro módulo deve ter sua própria lógica de descoberta de ativos.
OPERA consome o grafo via Context API.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.models.assets import (
    Asset, AssetCreate, AssetRelationCreate,
    AssetType, HierarchyLevel, RelationType,
    SemanticClassification,
)
from app.services.classifier import SemanticTagClassifier

log = logging.getLogger(__name__)

_ZONE_TO_PROCESS: dict[str, str] = {
    "Zona Baixa":   "distribution_low",
    "Zona Media":   "distribution_medium",
    "Zona Alta":    "distribution_high",
    "Zona Central": "distribution_central",
}


class AssetGraphBuilder:
    """
    Constrói grafo de ativos a partir de pares (device_id, tag_id) do Gateway.

    Resultado:
      assets:    lista de AssetCreate prontos para persistir
      relations: lista de AssetRelationCreate prontos para persistir
    """

    def __init__(self) -> None:
        self._classifier = SemanticTagClassifier()

    def build(
        self,
        devices: list[dict[str, Any]],   # [{"device_id": ..., "device_type": ..., "tags": [...]}]
    ) -> tuple[list[AssetCreate], list[AssetRelationCreate]]:

        all_tags = [
            f"{d['device_id']}.{t['tag_id']}"
            for d in devices
            for t in d.get("tags", [])
        ]

        classifications: dict[str, SemanticClassification] = {
            c.tag: c for c in self._classifier.classify_many(all_tags)
        }

        # Agrupa tags por device
        assets: list[AssetCreate] = []
        device_meta: dict[str, dict] = {}

        for dev in devices:
            dev_id   = dev["device_id"]
            dev_type = dev.get("device_type", "GENERIC").lower()
            dev_tags = [f"{dev_id}.{t['tag_id']}" for t in dev.get("tags", [])]

            # Infere tipo do ativo pela classificação dominante das tags
            type_votes: dict[AssetType, float] = defaultdict(float)
            zones: list[str] = []

            for tag in dev_tags:
                c = classifications.get(tag)
                if c:
                    type_votes[c.asset_type] += c.confidence
                    if c.zone:
                        zones.append(c.zone)

            best_type = (
                max(type_votes, key=lambda k: type_votes[k])
                if type_votes else AssetType.GENERIC
            )
            zone      = zones[0] if zones else None
            process   = _ZONE_TO_PROCESS.get(zone, None) if zone else None

            asset = AssetCreate(
                asset_id=dev_id,
                name=dev_id,
                level=HierarchyLevel.EQUIPMENT,
                asset_type=best_type,
                device_id=dev_id,
                metadata={
                    "inferred_zone":    zone,
                    "inferred_process": process,
                    "device_type_hint": dev_type,
                    "tag_count":        len(dev_tags),
                    "confidence":       round(
                        sum(type_votes.values()) / len(dev_tags) if dev_tags else 0, 3
                    ),
                },
            )
            assets.append(asset)
            device_meta[dev_id] = {
                "asset_type": best_type,
                "zone": zone,
                "process": process,
            }

        relations = self._infer_relations(device_meta)

        log.info("graph_builder.done assets=%d relations=%d", len(assets), len(relations))
        return assets, relations

    def _infer_relations(
        self, meta: dict[str, dict]
    ) -> list[AssetRelationCreate]:
        relations: list[AssetRelationCreate] = []

        reservoirs    = [k for k, v in meta.items() if v["asset_type"] == AssetType.RESERVOIR]
        pump_stations = [k for k, v in meta.items() if v["asset_type"] == AssetType.PUMP_STATION]
        vrps          = [k for k, v in meta.items() if v["asset_type"] == AssetType.VRP]

        # Reservatório → VRP (upstream)
        for res in reservoirs:
            for vrp in vrps:
                relations.append(AssetRelationCreate(
                    source_id=res, target_id=vrp,
                    relation_type=RelationType.UPSTREAM,
                    confidence=0.75,
                    metadata={"method": "hydraulic_prior"},
                ))

        # Reservatório → Bomba (feeds)
        for res in reservoirs:
            for pump in pump_stations:
                relations.append(AssetRelationCreate(
                    source_id=res, target_id=pump,
                    relation_type=RelationType.FEEDS,
                    confidence=0.80,
                    metadata={"method": "hydraulic_prior"},
                ))

        # Bomba → VRP (feeds)
        for pump in pump_stations:
            for vrp in vrps:
                pump_zone = meta[pump]["zone"]
                vrp_zone  = meta[vrp]["zone"]
                if pump_zone is None or pump_zone == vrp_zone:
                    relations.append(AssetRelationCreate(
                        source_id=pump, target_id=vrp,
                        relation_type=RelationType.FEEDS,
                        confidence=0.72,
                        metadata={"method": "hydraulic_prior"},
                    ))

        # VRPs na mesma zona → SAME_ZONE
        zone_map: dict[str, list[str]] = defaultdict(list)
        for dev_id, m in meta.items():
            if m["asset_type"] == AssetType.VRP and m["zone"]:
                zone_map[m["zone"]].append(dev_id)

        for zone, members in zone_map.items():
            for i, src in enumerate(members):
                for tgt in members[i + 1:]:
                    relations.append(AssetRelationCreate(
                        source_id=src, target_id=tgt,
                        relation_type=RelationType.SAME_ZONE,
                        confidence=0.85,
                        metadata={"zone": zone},
                    ))

        return relations
