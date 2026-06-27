-- 为 history 表添加 training_mode 字段
-- 在 Supabase Dashboard → SQL Editor 中执行此文件

ALTER TABLE history ADD COLUMN IF NOT EXISTS training_mode BOOLEAN DEFAULT FALSE;
