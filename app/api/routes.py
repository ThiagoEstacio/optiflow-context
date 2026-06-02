"""
OptiFlow Context REST API

Todos os módulos (Vision, OPERA, Historian) consultam aqui.
Nenhum módulo deve ter sua própria lógica de hierarquia de ativos.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.assets import (
    Asset, AssetContext, AssetCreate, AssetTree,
    AssetRelation, AssetRelationCreate,
    DiscoveryResult,
)
from app.services.discovery import DiscoveryService
from app.core.config import settings

router = APIRouter()


# ── Assets ────────────────────────────────────────────────────────────────────

@router.get("/assets", response_model=list[AssetContext])
async def list_assets(
    asset_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    await _sync_asset_context_from_historian(session)

    where = "WHERE 1=1"
    params: dict = {}
    if asset_type:
        where += " AND asset_type = :asset_type"; params["asset_type"] = asset_type

    rows = await session.execute(
        text(f"""
            SELECT asset_id, asset_type, functional_role, tags, confidence, last_seen
            FROM asset_context
            {where}
            ORDER BY asset_id
        """),
        params,
    )
    return [_asset_context_from_row(r._mapping) for r in rows]


@router.get("/assets/{asset_id}", response_model=AssetContext)
async def get_asset(asset_id: str, session: AsyncSession = Depends(get_session)):
    await _sync_asset_context_from_historian(session)

    row = await session.execute(
        text("""
            SELECT asset_id, asset_type, functional_role, tags, confidence, last_seen
            FROM asset_context
            WHERE asset_id = :id
        """),
        {"id": asset_id},
    )
    r = row.first()
    if not r:
        raise HTTPException(404, "Ativo não encontrado")
    return _asset_context_from_row(r._mapping)


@router.get("/assets/{asset_id}/tree", response_model=AssetTree)
async def get_asset_tree(asset_id: str, session: AsyncSession = Depends(get_session)):
    """Retorna o ativo com toda a subárvore de filhos e relações."""
    row = await session.execute(
        text("SELECT * FROM assets WHERE asset_id = :id"), {"id": asset_id}
    )
    r = row.first()
    if not r:
        raise HTTPException(404, "Ativo não encontrado")

    children = await _get_children(session, asset_id)
    relations = await _get_relations(session, asset_id)

    return AssetTree(**dict(r._mapping), children=children, relations=relations)


@router.get("/assets/by-device/{device_id}", response_model=Asset)
async def get_asset_by_device(device_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.execute(
        text("SELECT * FROM assets WHERE device_id = :d"), {"d": device_id}
    )
    r = row.first()
    if not r:
        raise HTTPException(404, f"Nenhum ativo para device_id={device_id}")
    return Asset(**dict(r._mapping))


@router.post("/assets", response_model=Asset, status_code=201)
async def create_asset(asset: AssetCreate, session: AsyncSession = Depends(get_session)):
    import json
    await session.execute(text("""
        INSERT INTO assets (asset_id, name, level, asset_type, parent_id,
                            device_id, tag_id, site_id, metadata)
        VALUES (:asset_id, :name, :level, :asset_type, :parent_id,
                :device_id, :tag_id, :site_id, :metadata::jsonb)
        ON CONFLICT (asset_id) DO UPDATE SET
            name=EXCLUDED.name, asset_type=EXCLUDED.asset_type,
            metadata=EXCLUDED.metadata, updated_at=now()
    """), {**asset.model_dump(exclude={"metadata"}), "metadata": json.dumps(asset.metadata)})
    await session.commit()
    return await _get_legacy_asset(session, asset.asset_id)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str, session: AsyncSession = Depends(get_session)):
    await session.execute(text("DELETE FROM assets WHERE asset_id = :id"), {"id": asset_id})
    await session.commit()


# ── Relations ─────────────────────────────────────────────────────────────────

@router.get("/relations", response_model=list[AssetRelation])
async def list_relations(
    source_id: str | None = None,
    relation_type: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    where = "WHERE 1=1"
    params: dict = {}
    if source_id:
        where += " AND source_id = :source_id"; params["source_id"] = source_id
    if relation_type:
        where += " AND relation_type = :relation_type"; params["relation_type"] = relation_type

    rows = await session.execute(
        text(f"SELECT * FROM asset_relations {where} ORDER BY source_id"), params
    )
    return [AssetRelation(**dict(r._mapping)) for r in rows]


@router.post("/relations", response_model=AssetRelation, status_code=201)
async def create_relation(
    rel: AssetRelationCreate,
    session: AsyncSession = Depends(get_session),
):
    import json
    await session.execute(text("""
        INSERT INTO asset_relations (source_id, target_id, relation_type, confidence, metadata)
        VALUES (:source_id, :target_id, :relation_type, :confidence, :metadata::jsonb)
        ON CONFLICT (source_id, target_id, relation_type) DO UPDATE SET
            confidence=EXCLUDED.confidence, metadata=EXCLUDED.metadata
        RETURNING id
    """), {**rel.model_dump(exclude={"metadata"}), "metadata": json.dumps(rel.metadata)})
    await session.commit()
    rows = await list_relations(rel.source_id, rel.relation_type, session)
    return rows[0]


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(text("SELECT * FROM asset_templates ORDER BY asset_type"))
    return [dict(r._mapping) for r in rows]


# ── Discovery ─────────────────────────────────────────────────────────────────

@router.post("/discover", response_model=DiscoveryResult)
async def run_discovery(session: AsyncSession = Depends(get_session)):
    """
    Consulta o Gateway (Connect), classifica semanticamente os devices e tags,
    constrói o grafo de ativos e persiste. Idempotente — pode rodar múltiplas vezes.
    """
    svc = DiscoveryService(gateway_url=settings.GATEWAY_URL)
    return await svc.run(session)


@router.get("/discovery/runs")
async def list_discovery_runs(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text("SELECT * FROM discovery_runs ORDER BY started_at DESC LIMIT 20")
    )
    return [dict(r._mapping) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_children(session: AsyncSession, asset_id: str) -> list[AssetTree]:
    rows = await session.execute(
        text("SELECT * FROM assets WHERE parent_id = :id ORDER BY name"), {"id": asset_id}
    )
    children = []
    for r in rows:
        sub_children = await _get_children(session, r.asset_id)
        sub_relations = await _get_relations(session, r.asset_id)
        children.append(AssetTree(**dict(r._mapping), children=sub_children, relations=sub_relations))
    return children


async def _get_relations(session: AsyncSession, asset_id: str) -> list[AssetRelation]:
    rows = await session.execute(
        text("SELECT * FROM asset_relations WHERE source_id = :id"), {"id": asset_id}
    )
    return [AssetRelation(**dict(r._mapping)) for r in rows]


async def _get_legacy_asset(session: AsyncSession, asset_id: str) -> Asset:
    row = await session.execute(
        text("SELECT * FROM assets WHERE asset_id = :id"), {"id": asset_id}
    )
    r = row.first()
    if not r:
        raise HTTPException(404, "Ativo não encontrado")
    return Asset(**dict(r._mapping))


async def _sync_asset_context_from_historian(session: AsyncSession) -> None:
    rows = await session.execute(text("""
        SELECT
            device_id AS asset_id,
            max(ts) AS last_seen,
            jsonb_agg(DISTINCT tag_id ORDER BY tag_id) AS tags
        FROM process_readings
        WHERE device_id IS NOT NULL
          AND device_id != ''
        GROUP BY device_id
    """))

    for row in rows:
        asset_id = row.asset_id
        asset_type, functional_role, confidence = _classify_asset(asset_id)
        tags = row.tags if isinstance(row.tags, str) else json.dumps(row.tags or [])
        await session.execute(text("""
            INSERT INTO asset_context
                (asset_id, asset_type, functional_role, tags, confidence, last_seen, updated_at)
            VALUES
                (:asset_id, :asset_type, :functional_role, CAST(:tags AS jsonb),
                 :confidence, :last_seen, now())
            ON CONFLICT (asset_id) DO UPDATE SET
                asset_type      = EXCLUDED.asset_type,
                functional_role = EXCLUDED.functional_role,
                tags            = EXCLUDED.tags,
                confidence      = EXCLUDED.confidence,
                last_seen       = EXCLUDED.last_seen,
                updated_at      = now()
        """), {
            "asset_id": asset_id,
            "asset_type": asset_type,
            "functional_role": functional_role,
            "tags": tags,
            "confidence": confidence,
            "last_seen": row.last_seen,
        })

    await session.commit()


def _classify_asset(asset_id: str) -> tuple[str, str, float]:
    if asset_id.startswith("VRP-"):
        return "pressure_regulator", "pressure_control", 0.95
    if asset_id.startswith("RES-"):
        return "reservoir", "storage_and_level_buffer", 0.95
    if asset_id.startswith("CRAT-"):
        return "pumping_station", "distribution_node", 0.85
    return "unknown", "unknown", 0.25


def _asset_context_from_row(row) -> AssetContext:
    tags = row["tags"] or []
    return AssetContext(
        asset_id=row["asset_id"],
        asset_type=row["asset_type"],
        functional_role=row["functional_role"],
        tags=tags,
        confidence=float(row["confidence"]),
        last_seen=row["last_seen"],
    )
