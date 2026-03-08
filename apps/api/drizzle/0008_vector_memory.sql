-- Vector memory store for semantic search (OpenClaw-style memory)
-- Stores chunked memory embeddings for hybrid BM25 + vector search

CREATE TABLE `memory_chunks` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `user_id` integer NOT NULL,
  `source_path` text NOT NULL,
  `section` text NOT NULL,
  `chunk_index` integer NOT NULL,
  `content` text NOT NULL,
  `embedding` text,
  `embedding_model` text,
  `token_count` integer NOT NULL DEFAULT 0,
  `start_line` integer NOT NULL DEFAULT 0,
  `end_line` integer NOT NULL DEFAULT 0,
  `checksum` text NOT NULL,
  `created_at` text DEFAULT 'CURRENT_TIMESTAMP',
  `updated_at` text DEFAULT 'CURRENT_TIMESTAMP'
);
--> statement-breakpoint
CREATE INDEX `idx_chunks_user` ON `memory_chunks` (`user_id`);
--> statement-breakpoint
CREATE INDEX `idx_chunks_source` ON `memory_chunks` (`user_id`, `source_path`);
--> statement-breakpoint
CREATE INDEX `idx_chunks_section` ON `memory_chunks` (`user_id`, `section`);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_chunks_unique` ON `memory_chunks` (`user_id`, `source_path`, `chunk_index`);
--> statement-breakpoint
CREATE TABLE `memory_daily_logs` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `user_id` integer NOT NULL,
  `date` text NOT NULL,
  `entry_count` integer NOT NULL DEFAULT 0,
  `last_flushed_at` text,
  `created_at` text DEFAULT 'CURRENT_TIMESTAMP',
  `updated_at` text DEFAULT 'CURRENT_TIMESTAMP'
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_daily_user_date` ON `memory_daily_logs` (`user_id`, `date`);
--> statement-breakpoint
CREATE TABLE `memory_search_config` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `user_id` integer NOT NULL,
  `embedding_provider` text NOT NULL DEFAULT 'openai',
  `embedding_model` text NOT NULL DEFAULT 'text-embedding-3-small',
  `hybrid_enabled` integer NOT NULL DEFAULT 1,
  `vector_weight` real NOT NULL DEFAULT 0.7,
  `text_weight` real NOT NULL DEFAULT 0.3,
  `temporal_decay_enabled` integer NOT NULL DEFAULT 1,
  `half_life_days` integer NOT NULL DEFAULT 30,
  `mmr_enabled` integer NOT NULL DEFAULT 1,
  `mmr_lambda` real NOT NULL DEFAULT 0.7,
  `max_results` integer NOT NULL DEFAULT 8,
  `created_at` text DEFAULT 'CURRENT_TIMESTAMP',
  `updated_at` text DEFAULT 'CURRENT_TIMESTAMP'
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_search_config_user` ON `memory_search_config` (`user_id`);
