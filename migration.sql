-- 数据库迁移脚本
-- 在 Supabase Dashboard → SQL Editor 中执行

ALTER TABLE history ADD COLUMN IF NOT EXISTS training_mode BOOLEAN DEFAULT FALSE;
ALTER TABLE laws ADD COLUMN IF NOT EXISTS modifier_map JSONB DEFAULT NULL;
