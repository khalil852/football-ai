-- 数据库迁移脚本
-- 逐行复制在 Supabase Dashboard → SQL Editor 执行

ALTER TABLE history ADD COLUMN IF NOT EXISTS training_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE laws ADD COLUMN IF NOT EXISTS triggers_count INTEGER DEFAULT 0;
ALTER TABLE laws ADD COLUMN IF NOT EXISTS correct_count INTEGER DEFAULT 0;

-- 新定律规则引擎字段
ALTER TABLE laws ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'attack';
ALTER TABLE laws ADD COLUMN IF NOT EXISTS trigger_keywords TEXT[] DEFAULT '{}';
ALTER TABLE laws ADD COLUMN IF NOT EXISTS effect_type TEXT DEFAULT 'multiply';
ALTER TABLE laws ADD COLUMN IF NOT EXISTS effect_value REAL DEFAULT 1.0;
ALTER TABLE laws ADD COLUMN IF NOT EXISTS effect_target TEXT DEFAULT 'attack';

-- 教练库
CREATE TABLE IF NOT EXISTS coaches (
    name TEXT PRIMARY KEY,
    team TEXT,
    nationality TEXT DEFAULT '',
    formation TEXT DEFAULT '',
    aggression REAL DEFAULT 1.0,
    style TEXT DEFAULT 'balanced',
    confidence REAL DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT NOW()
);
