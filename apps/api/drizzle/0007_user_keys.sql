CREATE TABLE `user_keys` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `user_id` integer NOT NULL,
  `kdf_salt` text NOT NULL,
  `kdf_time_cost` integer NOT NULL,
  `kdf_memory_cost_kib` integer NOT NULL,
  `kdf_parallelism` integer NOT NULL,
  `kdf_key_length` integer NOT NULL,
  `wrap_iv` text NOT NULL,
  `wrap_tag` text NOT NULL,
  `wrapped_dek` text NOT NULL,
  `created_at` text DEFAULT 'CURRENT_TIMESTAMP',
  `updated_at` text DEFAULT 'CURRENT_TIMESTAMP'
);
--> statement-breakpoint
CREATE UNIQUE INDEX `user_keys_user_id_unique` ON `user_keys` (`user_id`);
