"""
OptiFlow Context — Semantic Tag Classifier

Responsabilidade: traduzir nomes de tags industriais em significado operacional.
Migrado do OPERA (process_intelligence.semantic_classifier) para o Context,
que é o módulo responsável por "saber o que cada ativo é".

OPERA e Vision consultam o Context para obter essa classificação —
não implementam a sua própria.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.models.assets import AssetType, SemanticClassification, SignalVariable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Pattern:
    name:        str
    regex:       str
    asset_type:  AssetType
    variable:    SignalVariable
    criticality: str
    unit:        str | None
    confidence:  float
    zone_group:  int | None = None


_PATTERNS: list[_Pattern] = [
    # ── VRP / Válvula Redutora de Pressão ─────────────────────────────────────
    _Pattern("vrp_pressure_downstream",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](pj|p_down|pd)",
        AssetType.VRP, SignalVariable.PRESSURE_DOWNSTREAM, "high", "mca", 0.95, zone_group=2),
    _Pattern("vrp_pressure_upstream",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](pm|p_up|pu)",
        AssetType.VRP, SignalVariable.PRESSURE_UPSTREAM, "high", "mca", 0.95, zone_group=2),
    _Pattern("vrp_flow",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](vz|q|flow|vazao)",
        AssetType.VRP, SignalVariable.FLOW, "high", "L/s", 0.95, zone_group=2),
    _Pattern("vrp_valve_position",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](pos|position|abertura)",
        AssetType.VRP, SignalVariable.VALVE_POSITION, "medium", "%", 0.92, zone_group=2),
    _Pattern("vrp_setpoint",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](sp|setpoint)",
        AssetType.VRP, SignalVariable.SETPOINT, "high", "mca", 0.93, zone_group=2),
    _Pattern("vrp_status",
        r"(?i)(vrp|crm|kxo|scoa)[._\-]?(\w+)[._\-](status|online|onl)",
        AssetType.VRP, SignalVariable.STATUS, "critical", None, 0.92, zone_group=2),

    # ── Reservatório ──────────────────────────────────────────────────────────
    _Pattern("reservoir_level",
        r"(?i)(res|crat|reservoir|reservatorio)[._\-]?(\w+)[._\-](h|level|nivel|height)",
        AssetType.RESERVOIR, SignalVariable.LEVEL, "critical", "m", 0.95, zone_group=2),
    _Pattern("reservoir_flow_in",
        r"(?i)(res|crat|reservoir)[._\-]?(\w+)[._\-](q_in|qin|inflow|q_entrada)",
        AssetType.RESERVOIR, SignalVariable.FLOW, "high", "L/s", 0.93, zone_group=2),
    _Pattern("reservoir_flow_out",
        r"(?i)(res|crat|reservoir)[._\-]?(\w+)[._\-](q_out|qout|outflow|q_saida)",
        AssetType.RESERVOIR, SignalVariable.FLOW, "high", "L/s", 0.93, zone_group=2),
    _Pattern("reservoir_pumps",
        r"(?i)(res|crat|reservoir)[._\-]?(\w+)[._\-](bombas|pumps|pump_count)",
        AssetType.RESERVOIR, SignalVariable.PUMP_STATUS, "high", None, 0.90, zone_group=2),

    # ── Bomba / Estação de bombeamento ────────────────────────────────────────
    _Pattern("pump_status",
        r"(?i)(pump|bomba|eb|ebe)[._\-]?(\w+)[._\-](status|state|on|off|liga|desliga)",
        AssetType.PUMP_STATION, SignalVariable.PUMP_STATUS, "critical", None, 0.92, zone_group=2),
    _Pattern("pump_speed",
        r"(?i)(pump|bomba|eb)[._\-]?(\w+)[._\-](speed|rpm|velocidade|freq)",
        AssetType.PUMP_STATION, SignalVariable.PUMP_SPEED, "medium", "rpm", 0.90, zone_group=2),

    # ── Sensor genérico ───────────────────────────────────────────────────────
    _Pattern("sensor_temperature",
        r"(?i)(\w+)[._\-](temp|temperature|temperatura|t_)",
        AssetType.SENSOR, SignalVariable.TEMPERATURE, "low", "°C", 0.80),
    _Pattern("sensor_pressure_generic",
        r"(?i)(\w+)[._\-](pressure|pressao|press|pres)[._\-]?(\w*)",
        AssetType.SENSOR, SignalVariable.PRESSURE_DOWNSTREAM, "medium", "mca", 0.70),
]

# Mapa de lookup por device_id conhecido
_KNOWN_DEVICES: dict[str, tuple[AssetType, str | None]] = {
    "VRP":       (AssetType.VRP,          None),
    "RESERVOIR": (AssetType.RESERVOIR,    None),
    "CRAT":      (AssetType.RESERVOIR,    None),
    "RES":       (AssetType.RESERVOIR,    None),
    "PUMP":      (AssetType.PUMP_STATION, None),
    "EB":        (AssetType.PUMP_STATION, None),
}

_COMPILED = [(p, re.compile(p.regex)) for p in _PATTERNS]


class SemanticTagClassifier:
    """
    Classifica tags industriais em tipo de ativo e variável operacional.
    Usado internamente pelo AssetGraphBuilder durante discovery.
    """

    def classify(self, tag: str) -> SemanticClassification:
        for pattern, rx in _COMPILED:
            m = rx.search(tag)
            if not m:
                continue

            zone = None
            if pattern.zone_group is not None:
                try:
                    raw_zone = m.group(pattern.zone_group)
                    zone = _normalize_zone(raw_zone)
                except IndexError:
                    pass

            asset_id_hint = _infer_asset_id(tag, m)

            return SemanticClassification(
                tag=tag,
                asset_type=pattern.asset_type,
                variable=pattern.variable,
                zone=zone,
                criticality=pattern.criticality,
                confidence=pattern.confidence,
                pattern_matched=pattern.name,
                asset_id_hint=asset_id_hint,
                unit=pattern.unit,
            )

        # Fallback por device_type conhecido
        upper = tag.upper()
        for prefix, (atype, _) in _KNOWN_DEVICES.items():
            if upper.startswith(prefix):
                return SemanticClassification(
                    tag=tag, asset_type=atype,
                    variable=SignalVariable.UNKNOWN,
                    confidence=0.60, pattern_matched="device_type_prefix",
                )

        return SemanticClassification(
            tag=tag, asset_type=AssetType.UNKNOWN,
            variable=SignalVariable.UNKNOWN, confidence=0.10,
        )

    def classify_many(self, tags: list[str]) -> list[SemanticClassification]:
        return [self.classify(t) for t in tags]


def _normalize_zone(raw: str) -> str | None:
    raw = raw.upper().strip()
    mapping = {
        "BAIXA": "Zona Baixa", "LOW": "Zona Baixa",
        "MEDIA": "Zona Media", "MED": "Zona Media",
        "ALTA":  "Zona Alta",  "HIGH": "Zona Alta",
        "CENTRAL": "Zona Central",
    }
    return mapping.get(raw, raw if len(raw) > 2 else None)


def _infer_asset_id(tag: str, match: re.Match) -> str | None:
    parts = re.split(r"[._\-]", tag)
    if len(parts) >= 2:
        return parts[0] if len(parts) == 2 else f"{parts[0]}-{parts[1]}"
    return None
