-- 0007_judge_floor.sql: the database guard on a judge split's per-class floor in dev and test goes from 10 to 1, so the
-- code's floor (judges.MIN_PER_CLASS_EVAL, TRACE_LAB_JUDGE_EVAL_FLOOR, default 10) is the one that decides. Everywhere the
-- setting is unset the floor stays 10, as before; a local demo stack may lower it to see the Judges and Watch screens with
-- a handful of labels. Applied only by migrate.apply_pending. A correction is a new numbered file.
SET LOCAL lock_timeout = '3s';

ALTER TABLE tracelab.judge_splits DROP CONSTRAINT judge_splits_min_per_class_check;

ALTER TABLE tracelab.judge_splits ADD CONSTRAINT judge_splits_min_per_class_check CHECK (min_per_class >= 1);
