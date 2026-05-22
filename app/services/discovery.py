"""
OptiFlow Context — Discovery Service

Consulta o Gateway (Connect) via Management API para obter devices/tags,
executa o AssetGraphBuilder e persiste o resultado no banco de Context.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assets import AssetCreate, AssetRelationCreate, DiscoveryResult
from app.services.graph_builder import AssetGraphBuilder

log = logging.getLogger(__name__)


class DiscoveryService:

    def __init__(self, gateway_url: str):
        self._gateway_url = gateway_url
        self._builder     = AssetGraphBuilder()

    async def run(self, session: AsyncSession) -> DiscoveryResult:
        result = DiscoveryResult(run_id=str(uuid4()))
        errors: list[str] = []

        try:
            devices = await self._fetch_gateway_devices()
            result.sources = [f"{self._gateway_url}/api/devices"]
        except Exception as e:
            errors.append(f"gateway_fetch: {e}")
            result.errors  = errors
            result.completed_at = datetime.now(timezone.utc)
            await _save_run(session, result)
            return result

        assets, relations = self._builder.build(devices)

        # Persiste ativos (upsert)
        for asset in assets:
            await _upsert_asset(session, asset)

        # Persiste relações (upsert)
        for rel in relations:
            await _upsert_relation(session, rel)

        await session.commit()

        result.assets_discovered  = len(assets)
        result.relations_inferred = len(relations)
        result.tags_classified    = sum(len(d.get("tags", [])) for d in devices)
        result.completed_at       = datetime.now(timezone.utc)
        result.errors             = errors

        await _save_run(session, result)
        log.info("discovery.done assets=%d relations=%d", len(assets), len(relations))
        return result

    async def _fetch_gateway_devices(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"{self._gateway_url}/api/devices")
            r.raise_for_status()
            return r.json()


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _upsert_asset(session: AsyncSession, asset: AssetCreate) -> None:
    import json
    await session.execute(text("""
        INSERT INTO assets (asset_id, name, level, asset_type, parent_id,
                            device_id, tag_id, site_id, metadata)
        VALUES (:asset_id, :name, :level, :asset_type, :parent_id,
                :device_id, :tag_id, :site_id, :metadata::jsonb)
        ON CONFLICT (asset_id) DO UPDATE SET
            name       = EXCLUDED.name,
            asset_type = EXCLUDED.asset_type,
            metadata   = EXCLUDED.metadata,
            updated_at = now()
    """), {
        **asset.model_dump(exclude={"metadata"}),
        "metadata": json.dumps(asset.metadata),
    })


async def _upsert_relation(session: AsyncSession, rel: AssetRelationCreate) -> None:
    import json
    await session.execute(text("""
        INSERT INTO asset_relations (source_id, target_id, relation_type, confidence, metadata)
        VALUES (:source_id, :target_id, :relation_type, :confidence, :metadata::jsonb)
        ON CONFLICT (source_id, target_id, relation_type) DO UPDATE SET
            confidence = EXCLUDED.confidence,
            metadata   = EXCLUDED.metadata
    """), {
        **rel.model_dump(exclude={"metadata"}),
        "metadata": json.dumps(rel.metadata),
    })


async def _save_run(session: AsyncSession, result: DiscoveryResult) -> None:
    import json
    await session.execute(text("""
        INSERT INTO discovery_runs
            (run_id, started_at, completed_at, status,
             assets_discovered, relations_inferred, tags_classified, sources, errors)
        VALUES
            (:run_id, :started_at, :completed_at, :status,
             :assets_discovered, :relations_inferred, :tags_classified,
             :sources::jsonb, :errors::jsonb)
        ON CONFLICT (run_id) DO UPDATE SET
            completed_at        = EXCLUDED.completed_at,
            status              = EXCLUDED.status,
            assets_discovered   = EXCLUDED.assets_discovered,
            relations_inferred  = EXCLUDED.relations_inferred,
            tags_classified     = EXCLUDED.tags_classified,
            errors              = EXCLUDED.errors
    """), {
        "run_id":             result.run_id,
        "started_at":         result.started_at,
        "completed_at":       result.completed_at,
        "status":             "completed" if not result.errors else "partial",
        "assets_discovered":  result.assets_discovered,
        "relations_inferred": result.relations_inferred,
        "tags_classified":    result.tags_classified,
        "sources":            json.dumps(result.sources),
        "errors":             json.dumps(result.errors),
    })
    await session.commit()
