CREATE TABLE `discord_links` (
  `id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
  `channel_id` text NOT NULL,
  `user_id` integer NOT NULL,
  `created_at` text DEFAULT 'CURRENT_TIMESTAMP',
  `updated_at` text DEFAULT 'CURRENT_TIMESTAMP'
);
--> statement-breakpoint
CREATE UNIQUE INDEX `discord_links_channel_id_unique` ON `discord_links` (`channel_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX `discord_links_user_id_unique` ON `discord_links` (`user_id`);
