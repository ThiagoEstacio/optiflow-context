-- OptiFlow Context — Schema de Ativos
-- Hierarquia ISA-95: Enterprise → Site → Area → Unit → Equipment → Tag
-- Fonte da verdade para Vision, OPERA e qualquer módulo que precise de contexto de ativo.

-- ── Hierarquia de ativos ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assets (
    asset_id    TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL,
    level       TEXT        NOT NULL,   -- enterprise|site|area|unit|equipment|tag
    asset_type  TEXT        NOT NULL DEFAULT 'generic',
    parent_id   TEXT        REFERENCES assets(asset_id) ON DELETE SET NULL,
    device_id   TEXT,                   -- link para DeviceRegistry do Connect (equipment)
    tag_id      TEXT,                   -- link para tag do field (tag level)
    site_id     TEXT,                   -- código de site (ex: BR/SP/SAO)
    metadata    JSONB       NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assets_parent    ON assets (parent_id);
CREATE INDEX IF NOT EXISTS idx_assets_level     ON assets (level);
CREATE INDEX IF NOT EXISTS idx_assets_device_id ON assets (device_id);
CREATE INDEX IF NOT EXISTS idx_assets_site_id   ON assets (site_id);

-- ── Contexto semântico mínimo derivado do Historian ──────────────────────────
-- Sprint 1B: registro básico de ativos observados em process_readings.

CREATE TABLE IF NOT EXISTS asset_context (
    asset_id        TEXT        PRIMARY KEY,
    asset_type      TEXT        NOT NULL,
    functional_role TEXT        NOT NULL,
    tags            JSONB       NOT NULL DEFAULT '[]',
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    last_seen       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asset_context_type ON asset_context (asset_type);
CREATE INDEX IF NOT EXISTS idx_asset_context_last_seen ON asset_context (last_seen DESC);

-- ── Relações entre ativos (além de parent-child) ──────────────────────────────

CREATE TABLE IF NOT EXISTS asset_relations (
    id             SERIAL      PRIMARY KEY,
    source_id      TEXT        NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    target_id      TEXT        NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    relation_type  TEXT        NOT NULL,  -- feeds|upstream|same_zone|controlled_by|...
    confidence     FLOAT       NOT NULL DEFAULT 1.0,
    metadata       JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON asset_relations (source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON asset_relations (target_id);

-- ── Templates de ativo (equivalente ao AF Template do PI) ────────────────────
-- Define atributos esperados para cada tipo de equipamento.

CREATE TABLE IF NOT EXISTS asset_templates (
    template_id  TEXT        PRIMARY KEY,
    asset_type   TEXT        NOT NULL,
    name         TEXT        NOT NULL,
    description  TEXT,
    attributes   JSONB       NOT NULL DEFAULT '[]',
    -- attributes: [{tag_id, variable, unit, description, formula, criticality}]
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Templates padrão da plataforma
INSERT INTO asset_templates (template_id, asset_type, name, description, attributes)
VALUES
  ('tpl_vrp', 'vrp', 'VRP — Válvula Redutora de Pressão',
   'Equipamento de controle de pressão em rede de distribuição de água',
   '[
     {"tag_id":"pj",  "variable":"pressure_downstream","unit":"mca","criticality":"high"},
     {"tag_id":"pm",  "variable":"pressure_upstream",  "unit":"mca","criticality":"high"},
     {"tag_id":"vz",  "variable":"flow",               "unit":"L/s","criticality":"high"},
     {"tag_id":"pos", "variable":"valve_position",     "unit":"%",  "criticality":"medium"},
     {"tag_id":"sp",  "variable":"setpoint",           "unit":"mca","criticality":"high"},
     {"variable":"pressure_deviation","op":"sub","operands":["pj","sp"],"unit":"mca","criticality":"high","computed":true}
   ]'::jsonb),
  ('tpl_reservoir', 'reservoir', 'Reservatório',
   'Reservatório de armazenamento de água tratada',
   '[
     {"tag_id":"h",      "variable":"level",  "unit":"m",  "criticality":"critical"},
     {"tag_id":"q_in",   "variable":"flow",   "unit":"L/s","criticality":"high"},
     {"tag_id":"q_out",  "variable":"flow",   "unit":"L/s","criticality":"high"},
     {"tag_id":"bombas", "variable":"pump_status","unit":"","criticality":"high"}
   ]'::jsonb),
  ('tpl_pump_station', 'pump_station', 'Estação de Bombeamento',
   'Conjunto de bombas para adução ou recalque',
   '[
     {"tag_id":"status", "variable":"pump_status","unit":"",    "criticality":"critical"},
     {"tag_id":"speed",  "variable":"pump_speed", "unit":"rpm", "criticality":"medium"},
     {"tag_id":"q",      "variable":"flow",       "unit":"L/s", "criticality":"high"}
   ]'::jsonb),
  ('tpl_macromedidor', 'macromedidor', 'Macromedidor de Zona',
   'Medição agregada de zona de pressão: VRPs online, total, pressão jusante média e vazão',
   '[
     {"tag_id":"p_jusante_media_mca","variable":"pressure_downstream","unit":"mca","criticality":"high"},
     {"tag_id":"q_lps",              "variable":"flow",               "unit":"L/s","criticality":"high"},
     {"tag_id":"n_online",           "variable":"vrps_online",        "unit":"",   "criticality":"medium"},
     {"tag_id":"n_vrps",             "variable":"vrps_total",         "unit":"",   "criticality":"low"}
   ]'::jsonb)
ON CONFLICT (template_id) DO NOTHING;

-- ── Discovery runs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS discovery_runs (
    run_id              TEXT        PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,
    status              TEXT        NOT NULL DEFAULT 'running',
    assets_discovered   INT         NOT NULL DEFAULT 0,
    relations_inferred  INT         NOT NULL DEFAULT 0,
    tags_classified     INT         NOT NULL DEFAULT 0,
    sources             JSONB       NOT NULL DEFAULT '[]',
    errors              JSONB       NOT NULL DEFAULT '[]'
);
