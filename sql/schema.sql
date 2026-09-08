-- lithium.db schema export (auto-generated 2026-09-08)
-- tables: 35

-- ==== agent_action_log ====
CREATE TABLE agent_action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            prev_position INTEGER,
            new_position INTEGER,
            delta INTEGER,
            prev_view_score INTEGER,
            new_view_score INTEGER,
            trigger TEXT,
            reasoning TEXT,
            close_price REAL,
            pnl_unreal REAL,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );

-- ==== agent_custom_data ====
CREATE TABLE agent_custom_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,           -- NULL = 全局，影响所有agent
            date TEXT NOT NULL,
            data_type TEXT NOT NULL, -- 'manual_signal' / 'external_report' / 'field_note'
            source TEXT,             -- 来源
            content TEXT NOT NULL,
            score_impact INTEGER,    -- 预估对观点影响（-20~+20）
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

-- ==== agent_decisions ====
CREATE TABLE agent_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            trigger TEXT,            -- 'stop_loss' / 'target_price' / 'event' / 'llm_review' / 'manual'
            action TEXT,             -- 'hold' / 'add' / 'reduce' / 'reverse'
            delta_position INTEGER,
            reason TEXT,
            model_used TEXT,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );

-- ==== agent_events ====
CREATE TABLE agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    event_type TEXT,
    summary TEXT,
    severity INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ==== agent_game_factors ====
CREATE TABLE agent_game_factors (
            date TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            base_score INTEGER,
            info_transfer REAL,
            behavioral_impact REAL,
            total_correction REAL,
            corrected_score INTEGER,
            factors_detail TEXT,
            PRIMARY KEY (date, agent_id)
        );

-- ==== agent_history ====
CREATE TABLE agent_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            position INTEGER,
            avg_cost REAL,
            view_score INTEGER,
            view_text TEXT,
            pnl_unreal REAL,
            close REAL,
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );

-- ==== agent_interactions ====
CREATE TABLE agent_interactions (
        date              TEXT NOT NULL,
        from_agent        TEXT NOT NULL,
        to_agent          TEXT NOT NULL,
        influence_type    TEXT NOT NULL,
        magnitude         REAL NOT NULL,
        PRIMARY KEY (date, from_agent, to_agent, influence_type)
    );

-- ==== agent_psychology ====
CREATE TABLE agent_psychology (
    date           TEXT    NOT NULL,
    agent_id       TEXT    NOT NULL,
    state          TEXT    NOT NULL,
    anchor_price   REAL,
    buy_zone_low   REAL,
    buy_zone_high  REAL,
    trigger_desc   TEXT,
    score          REAL,
    state_color    TEXT,
    PRIMARY KEY (date, agent_id)
);

-- ==== agent_view_log ====
CREATE TABLE agent_view_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL,
            date TEXT NOT NULL,
            view_score INTEGER,
            view_text TEXT,
            daily_reasoning TEXT,    -- 当日决策简要推理（规则层输出）
            key_indicators TEXT,     -- JSON: {"smelter_profit": -420, ...}
            daily_actions TEXT,      -- JSON: [{"action": "reduce", "reason": "..."}]
            FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
        );

-- ==== agents ====
CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL,           -- smelter/merchant/institution/arbitrage/retail/resource/policy
            capital REAL DEFAULT 10000,   -- 初始资金（万元）
            position INTEGER DEFAULT 0,   -- 当前持仓（手），正=多/负=空
            avg_cost REAL DEFAULT 0,
            target_long REAL DEFAULT 0,   -- 心理目标价（多）
            target_short REAL DEFAULT 0,  -- 心理目标价（空）
            stop_loss REAL DEFAULT 0,     -- 止损价
            take_profit REAL DEFAULT 0,   -- 止盈价
            view_score INTEGER DEFAULT 0, -- -100(极度空头) ~ +100(极度多头)
            view_text TEXT,               -- 观点一句话
            needs_review INTEGER DEFAULT 0, -- 是否需要LLM审核
            risk_params TEXT DEFAULT '{}', -- JSON: 风险偏好、持仓上限等
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        , pnl_unreal REAL DEFAULT 0);

-- ==== behavior_models ====
CREATE TABLE behavior_models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL UNIQUE,
  profit_source TEXT NOT NULL DEFAULT '',
  position_bias TEXT DEFAULT '双向' CHECK(position_bias IN ('做多','做空','双向')),
  risk_tolerance INTEGER DEFAULT 50 CHECK(risk_tolerance BETWEEN 0 AND 100),
  reaction_speed INTEGER DEFAULT 50 CHECK(reaction_speed BETWEEN 0 AND 100),
  herd_tendency INTEGER DEFAULT 50 CHECK(herd_tendency BETWEEN 0 AND 100),
  conviction_decay INTEGER DEFAULT 50 CHECK(conviction_decay BETWEEN 0 AND 100),
  entry_rules TEXT DEFAULT '[]',
  exit_rules TEXT DEFAULT '[]',
  risk_rules TEXT DEFAULT '[]',
  known_info TEXT DEFAULT '[]',
  blind_info TEXT DEFAULT '[]',
  current_position REAL DEFAULT 0,
  avg_cost REAL DEFAULT 0,
  last_action TEXT DEFAULT '',
  pnl REAL DEFAULT 0,
  description TEXT DEFAULT '',
  updated_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY (participant_id) REFERENCES participants(id)
);

-- ==== cards ====
CREATE TABLE cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    sentiment   TEXT    NOT NULL CHECK(sentiment IN ('利多','利空')),
    strength    INTEGER NOT NULL DEFAULT 3 CHECK(strength BETWEEN 1 AND 5),
    entry_date  TEXT    NOT NULL,
    target_price REAL,
    category    TEXT    DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active','consumed')),
    note_id     INTEGER REFERENCES notes(id),
    description TEXT    DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now','localtime'))
);

-- ==== contract_daily_all ====
CREATE TABLE contract_daily_all (
        contract TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume INTEGER DEFAULT 0,
        hold INTEGER DEFAULT 0,
        settle REAL,
        PRIMARY KEY (contract, date)
    );

-- ==== contract_prices ====
CREATE TABLE contract_prices (
            date TEXT,
            contract TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER DEFAULT 0,
            position INTEGER DEFAULT 0,
            settle REAL DEFAULT 0,
            PRIMARY KEY (date, contract)
        );

-- ==== fundamental_indices ====
CREATE TABLE fundamental_indices (
        date TEXT PRIMARY KEY, supply REAL, demand REAL, inventory REAL,
        profit REAL, sentiment REAL, basis REAL, composite REAL, n_dims INTEGER);

-- ==== info_access ====
CREATE TABLE info_access (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  info_category TEXT NOT NULL,
  access_level INTEGER DEFAULT 50 CHECK(access_level BETWEEN 0 AND 100),
  delay_days INTEGER DEFAULT 0,
  FOREIGN KEY (participant_id) REFERENCES participants(id)
);

-- ==== info_categories ====
CREATE TABLE info_categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT DEFAULT ''
);

-- ==== interpretations ====
CREATE TABLE interpretations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        note_id INTEGER NOT NULL,
        participant_id INTEGER NOT NULL,
        interpretation TEXT DEFAULT '',
        sentiment TEXT DEFAULT '中性' CHECK(sentiment IN ('利多','利空','中性')),
        confidence INTEGER DEFAULT 50,
        action_bias TEXT DEFAULT '观望',
        reasoning TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (note_id) REFERENCES notes(id),
        FOREIGN KEY (participant_id) REFERENCES participants(id)
    );

-- ==== inventory_history ====
CREATE TABLE inventory_history (
            date        TEXT    PRIMARY KEY,
            inventory   INTEGER DEFAULT 0,
            change      INTEGER DEFAULT 0,
            source      TEXT    DEFAULT 'em'
        );

-- ==== lithium_daily_prices ====
CREATE TABLE lithium_daily_prices (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        );

-- ==== lithium_meta ====
CREATE TABLE lithium_meta (
            sheet_name   TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            key          TEXT NOT NULL,
            value        TEXT NOT NULL,
            PRIMARY KEY (sheet_name, col_idx, key)
        );

-- ==== lithium_monthly ====
CREATE TABLE lithium_monthly (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        );

-- ==== lithium_weekly ====
CREATE TABLE lithium_weekly (
            date         TEXT NOT NULL,
            col_idx      INTEGER NOT NULL,
            value        REAL,
            PRIMARY KEY (date, col_idx)
        );

-- ==== logic_scores ====
CREATE TABLE logic_scores (
                date        TEXT    NOT NULL,
                logic_name  TEXT    NOT NULL,
                avg_score   REAL    NOT NULL,
                min_score   REAL    NOT NULL,
                max_score   REAL    NOT NULL,
                agent_count INTEGER DEFAULT 0,
                PRIMARY KEY (date, logic_name)
            );

-- ==== market_signals ====
CREATE TABLE market_signals (
                date      TEXT NOT NULL,
                signal    TEXT NOT NULL,
                value     REAL,
                change    REAL,
                strength  REAL,
                direction INTEGER,
                detail    TEXT,
                PRIMARY KEY (date, signal)
            );

-- ==== minute_prices ====
CREATE TABLE minute_prices (
            datetime    TEXT    NOT NULL,
            contract    TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            PRIMARY KEY (datetime, contract)
        );

-- ==== notes ====
CREATE TABLE notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            content     TEXT    DEFAULT '',
            sentiment   TEXT    DEFAULT '中性'
                CHECK(sentiment IN ('利多','利空','中性')),
            tags        TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT (datetime('now','localtime')),
            updated_at  TEXT    DEFAULT (datetime('now','localtime'))
        );

-- ==== paichan_visits ====
CREATE TABLE paichan_visits (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ip          TEXT    NOT NULL,
            user_agent  TEXT,
            referrer    TEXT,
            visited_at  TEXT    DEFAULT (datetime('now','localtime')),
            notified    INTEGER DEFAULT 0
        );

-- ==== participant_positions ====
CREATE TABLE participant_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  participant_id INTEGER NOT NULL,
  position_type TEXT NOT NULL,
  direction TEXT NOT NULL,
  display_label TEXT,
  price_level REAL,
  price_high REAL,
  price_low REAL,
  trigger_date TEXT,
  trigger_condition TEXT,
  strength INTEGER DEFAULT 3,
  description TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

-- ==== participants ====
CREATE TABLE participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, short_name TEXT NOT NULL,
        description TEXT DEFAULT '', strategy TEXT DEFAULT '',
        info_priorities TEXT DEFAULT ''
    );

-- ==== periods ====
CREATE TABLE periods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date  TEXT    NOT NULL,
            end_date    TEXT    NOT NULL,
            theme       TEXT    NOT NULL,
            color       TEXT    DEFAULT '#ff6b6b',
            description TEXT    DEFAULT ''
        );

-- ==== prices ====
CREATE TABLE prices (
            date        TEXT    PRIMARY KEY,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            settle      REAL    DEFAULT 0
        );

-- ==== simulation_log ====
CREATE TABLE simulation_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id INTEGER,
  participant_id INTEGER NOT NULL,
  before_position REAL DEFAULT 0,
  after_position REAL DEFAULT 0,
  action TEXT DEFAULT '',
  price REAL DEFAULT 0,
  reasoning TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY (note_id) REFERENCES notes(id),
  FOREIGN KEY (participant_id) REFERENCES participants(id)
);

-- ==== spot_price ====
CREATE TABLE spot_price (
            date              TEXT    NOT NULL,
            variety           TEXT    NOT NULL DEFAULT 'LC',
            spot_price        REAL,
            near_symbol       TEXT,
            near_price        REAL,
            dom_symbol        TEXT,
            dom_price         REAL,
            near_basis        REAL,
            dom_basis         REAL,
            near_basis_rate   REAL,
            dom_basis_rate    REAL,
            PRIMARY KEY (date, variety)
        );

-- ==== spreads ====
CREATE TABLE spreads (
            date TEXT,
            near_contract TEXT,
            far_contract TEXT,
            spread REAL,
            PRIMARY KEY (date, near_contract, far_contract)
        );
