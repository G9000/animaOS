# Portable State Inventory

This inventory is the checked placement contract for persisted ANIMA state. It
describes the destination after the portable-Core cutover, not merely where a
value happens to live today. The inventory is deliberately fail-closed: schema
tests compare every SQLCipher and Runtime column with the exact field lists
below, and frontend/source-contract tests compare every browser key, app-data
file, mod-store family, and credential call with the non-database sections.

Destination terms:

- `soul`: durable encrypted identity, memory, emotional, or growth state in
  SQLCipher Soul.
- `soul-keyslots`: Soul-internal encrypted key records. Legacy `user_keys`
  rows are converted and then retired.
- `account-profile`: encrypted CoreFS `account-profile` object. The legacy
  password hash is an exception listed below and is removed.
- `corefs-object`: encrypted CoreFS object of the record's declared kind.
- `corefs-preferences`: encrypted CoreFS `preferences` object.
- `runtime-machine-local`: non-portable operational state. Private payloads
  are sealed or kept only in unlock-scoped memory; safe metadata may remain in
  the Runtime database.
- `device-*`: non-portable application data bound to this installation.
- `os-credential`: secret material available only through the platform
  credential service. Absence of a secure backend is an error.
- `session-only`: process/tab-lifetime state that is cleared on lock/logout.
- `remove-*`: legacy or derived state that is verified against its target and
  then scrubbed.

The machine-readable records use `store|record|comma-separated fields|target`.
Fields may be split across multiple records only when the row has mixed
semantics. A field must appear exactly once.

<!-- portable-state-inventory:v1:start -->
```text
sqlcipher|agent_experiences|id,user_id,task_intent,approach,quality_score,source_thread_id,source_run_id,tool_names_json,turn_count,cluster_id,superseded_by,created_at,updated_at|soul
sqlcipher|agent_experiences|embedding_json|remove-derived
sqlcipher|agent_messages|id,thread_id,run_id,step_id,sequence_id,role,content_text,content_json,tool_name,tool_call_id,tool_args_json,is_in_context,token_estimate,source,created_at|corefs-object
sqlcipher|agent_profile|id,user_id,agent_name,creator_name,relationship,agent_type,avatar_url,agent_birthday,thinking_monologue_json,created_at,updated_at|soul
sqlcipher|agent_profile|setup_complete|account-profile
sqlcipher|agent_runs|id,thread_id,user_id,provider,model,mode,status,stop_reason,error_text,started_at,completed_at,prompt_tokens,completion_tokens,total_tokens,pending_approval_message_id|runtime-machine-local
sqlcipher|agent_skills|id,user_id,cluster_id,name,description,content,confidence,experience_count,last_refined_at,superseded_by,created_at,updated_at|soul
sqlcipher|agent_skills|embedding_json|remove-derived
sqlcipher|agent_steps|id,run_id,thread_id,step_index,status,request_json,response_json,tool_calls_json,usage_json,error_text,created_at|runtime-machine-local
sqlcipher|agent_threads|id,user_id,status,title,created_at,updated_at,last_message_at,next_message_sequence|corefs-object
sqlcipher|background_task_runs|id,user_id,task_type,status,result_json,error_message,started_at,completed_at,created_at|runtime-machine-local
sqlcipher|core_emotional_patterns|id,user_id,pattern,dominant_emotion,trigger_context,frequency,confidence,first_observed,last_observed|soul
sqlcipher|corefs_writing_source_state|user_id,generation|runtime-machine-local
sqlcipher|diary_attachments|id,entry_id,user_id,kind,mime_type,size_bytes,storage_path,original_filename,caption,sha256,created_at|corefs-object
sqlcipher|diary_entries|id,user_id,entry_date,title,body,mood,source,cover_attachment_id,folder_id,created_at,updated_at|corefs-object
sqlcipher|diary_folders|id,user_id,name,created_at|corefs-object
sqlcipher|discord_links|id,channel_id,user_id,created_at|device-integration-registry
sqlcipher|dream_journal|id,user_id,dreamt_at,narrative,source_refs,affect_delta,share_worthy,surfaced,claimed_at,created_at|soul
sqlcipher|emotional_signals|id,user_id,thread_id,emotion,confidence,evidence_type,evidence,trajectory,previous_emotion,topic,acted_on,created_at|soul
sqlcipher|experience_cluster_state|id,user_id,state_json,created_at,updated_at|soul
sqlcipher|foresight_signals|id,user_id,content,evidence,relative_text,start_date,end_date,duration_days,status,confidence,source_thread_id,source_message_ids_json,observed_at,last_seen_at,created_at,updated_at|soul
sqlcipher|forget_audit_log|id,user_id,forgotten_at,trigger,scope,items_forgotten,derived_refs_affected|soul
sqlcipher|growth_log|id,user_id,entry,source,created_at|soul
sqlcipher|identity_blocks|id,user_id,content,version,updated_by,metadata_json,created_at,updated_at|soul
sqlcipher|initiative_log|id,user_id,fired_at,drive,pressure_snapshot,gate_states,generated_text,delivered,answered,created_at|soul
sqlcipher|kg_entities|id,user_id,name,name_normalized,entity_type,description,mentions,aliases_json,created_at,updated_at|soul
sqlcipher|kg_entities|embedding_json,embedding_checksum|remove-derived
sqlcipher|kg_relations|id,user_id,source_id,destination_id,relation_type,mentions,source_memory_id,evidence_id,observed_at,valid_from,valid_to,confidence,status,supersedes_relation_id,evolves_from_relation_id,created_at,updated_at|soul
sqlcipher|latent_traces|id,user_id,topic_key,kind,weight,evidence_refs,first_seen,last_seen|soul
sqlcipher|memory_claim_evidence|id,claim_id,source_text,source_kind,created_at|soul
sqlcipher|memory_claims|id,user_id,subject_type,namespace,slot,value_text,value_json,polarity,confidence,status,canonical_key,source_kind,extractor,memory_item_id,superseded_by_id,created_at,updated_at|soul
sqlcipher|memory_episodes|id,user_id,thread_id,date,time,topics_json,summary,emotional_arc,significance_score,turn_count,message_indices_json,segmentation_method,transcript_ref,created_at|soul
sqlcipher|memory_episodes|needs_regeneration|runtime-machine-local
sqlcipher|memory_item_evidence|id,user_id,memory_item_id,source_kind,runtime_thread_id,runtime_message_id,runtime_message_ids_json,transcript_ref,sequence_id,speaker,observed_at,source_created_at,confidence,extractor,evidence_text,metadata_json,created_at|soul
sqlcipher|memory_item_tags|id,tag,item_id,user_id,created_at|soul
sqlcipher|memory_items|id,user_id,content,category,importance,source,superseded_by,last_referenced_at,reference_count,tags_json,memory_class,emotional_salience,stability_class,decay_class,relationship_proximity,evidence_strength,evolves_from_item_id,evolution_kind,heat,created_at,updated_at,distilled_at,reconsolidation_drift|soul
sqlcipher|memory_items|embedding_json,embedding_checksum|remove-derived
sqlcipher|memory_vectors|item_id,user_id,content,category,importance,embedding|remove-derived
sqlcipher|presence_configs|id,user_id,enabled,main_chat_enabled,home_greeting_context_enabled,task_nudges_enabled,memory_nudges_enabled,checkin_nudges_enabled,custom_instruction,initiative_enabled,quiet_hours_start,quiet_hours_end,dream_sharing,created_at,updated_at|corefs-preferences
sqlcipher|reconsolidation_log|id,user_id,memory_item_id,applied_at,field,old_value,new_value,eta|soul
sqlcipher|self_model_blocks|id,user_id,section,content,version,updated_by,metadata_json,created_at,updated_at|soul
sqlcipher|self_model_blocks|needs_regeneration|runtime-machine-local
sqlcipher|soul_keyslots|id,owner_id,domain,wrapping_path,key_version,credential_generation,status,kdf_algorithm,wrap_algorithm,envelope_version,kdf_salt,kdf_time_cost,kdf_memory_cost_kib,kdf_parallelism,kdf_key_length,wrap_iv,wrap_tag,wrapped_dek,created_at,updated_at|soul-keyslots
sqlcipher|tasks|id,user_id,text,done,priority,due_date,completed_at,created_at,updated_at|corefs-object
sqlcipher|telegram_links|id,chat_id,user_id,created_at|device-integration-registry
sqlcipher|tendency_contributions|id,user_id,tombstone_item_id,tendency_claim_id,contribution_vector,created_at|soul
sqlcipher|user_keys|id,user_id,domain,kdf_salt,kdf_time_cost,kdf_memory_cost_kib,kdf_parallelism,kdf_key_length,wrap_iv,wrap_tag,wrapped_dek,created_at,updated_at|soul-keyslots
sqlcipher|user_profile_field_evidence|id,profile_field_id,user_id,source_kind,source_memory_id,source_evidence_id,source_claim_evidence_id,runtime_thread_id,runtime_message_id,evidence_text,observed_at,created_at|soul
sqlcipher|user_profile_fields|id,user_id,category,key,value_text,confidence,status,source_kind,source_memory_id,source_evidence_id,source_claim_evidence_id,superseded_by_id,first_observed_at,last_observed_at,created_at,updated_at|soul
sqlcipher|users|id,username,display_name,gender,age,birthday,created_at,updated_at|account-profile
sqlcipher|users|password_hash|remove-legacy-auth
runtime-db|active_intentions|id,user_id,content,version,updated_by,updated_at|runtime-machine-local
runtime-db|affect_state|id,user_id,valence,arousal,energy,arousal_baseline_shift,high_arousal_hours,updated_at|runtime-machine-local
runtime-db|corefs_blind_tokens|id,core_id,local_instance_id,family,generation,token,object_id,object_id_hash,revision_hash,created_at|runtime-machine-local
runtime-db|corefs_index_checkpoints|id,core_id,local_instance_id,family,catalog_generation,index_version,cursor_hash,completed_count,total_count,status,error_code,error_digest,updated_at|runtime-machine-local
runtime-db|corefs_index_entries|id,core_id,local_instance_id,family,object_id_hash,revision_hash,catalog_generation,index_version,status,checksum,created_at,updated_at|runtime-machine-local
runtime-db|corefs_migration_journal|id,core_id,local_instance_id,converter_id,source_id_hash,batch_cursor_hash,source_checksum,target_checksum,migrated_count,status,error_code,error_digest,created_at,updated_at|runtime-machine-local
runtime-db|corefs_runtime_binding|binding_slot,core_id,local_instance_id,created_at,updated_at|runtime-machine-local
runtime-db|corefs_sealed_payloads|id,core_id,local_instance_id,row_type,row_id_hash,owner_id_hash,key_version,nonce,ciphertext,aad_digest,created_at,updated_at|runtime-machine-local
runtime-db|contradiction_checks|id,user_id,pair_hash,verdict,checked_at|runtime-machine-local
runtime-db|current_emotions|id,user_id,thread_id,emotion,confidence,evidence_type,evidence,trajectory,previous_emotion,topic,acted_on,created_at|runtime-machine-local
runtime-db|drive_states|id,user_id,unresolved_thread,pattern_insight,relational,novelty,dream_residue,last_fired_at,last_user_turn_at,pattern_insight_surfaced_at,pattern_insight_surfaced_id,last_dream_attempt_at,unanswered_initiatives,starvation_losses,updated_at|runtime-machine-local
runtime-db|embedding_config|id,embedding_model,embedding_dim,reembed_required,updated_at|runtime-machine-local
runtime-db|embeddings|id,user_id,source_type,source_id,content_hash,embedding_checksum,embedding,content_preview,category,importance,created_at,updated_at|runtime-machine-local
runtime-db|memory_access_log|id,user_id,memory_item_id,accessed_at,synced|runtime-machine-local
runtime-db|memory_candidates|id,user_id,content,category,importance,importance_source,supersedes_item_id,source,source_message_ids,extraction_model,tags_json,salience_json,content_hash,status,last_error,retry_count,created_at,processed_at|runtime-machine-local
runtime-db|memory_extraction_failures|id,user_id,source_message_ids,user_message_preview,assistant_response_preview,failure_reason,extraction_model,status,retry_count,last_attempt_at,resolved_at,created_at,updated_at|runtime-machine-local
runtime-db|memory_retrieval_feedback|id,user_id,run_id,memory_item_id,was_used,was_corrected,evidence_score,created_at,synced|runtime-machine-local
runtime-db|pending_initiatives|id,user_id,initiative_log_id,drive,text,delivered,acknowledged,created_at,acknowledged_at|runtime-machine-local
runtime-db|pending_memory_ops|id,user_id,op_type,target_block,content,old_content,source_run_id,source_tool_call_id,created_at,consolidated,consolidated_at,failed,failure_reason,retry_count,content_hash|runtime-machine-local
runtime-db|presence_catchup|id,user_id,gap_seconds,components,dream_deferred,created_at|runtime-machine-local
runtime-db|profile_update_candidates|id,user_id,category,key,value,confidence,evidence_text,source,source_message_ids,extraction_model,content_hash,status,last_error,retry_count,created_at,processed_at|runtime-machine-local
runtime-db|promotion_journal|id,user_id,candidate_id,pending_op_id,decision,reason,target_table,target_record_id,content_hash,extraction_model,journal_status,created_at|runtime-machine-local
runtime-db|runtime_background_task_runs|id,user_id,task_type,status,result_json,error_message,started_at,completed_at,created_at|runtime-machine-local
runtime-db|runtime_consolidation_cursors|id,user_id,thread_id,last_processed_message_id,messages_processed,updated_at|runtime-machine-local
runtime-db|runtime_document_chunks|id,document_id,user_id,chunk_index,content_text,content_char_count,content_hash,page_start,page_end,section_title,token_count,parse_quality,metadata_json,created_at,updated_at|runtime-machine-local
runtime-db|runtime_documents|id,user_id,thread_id,workflow_run_id,filename,mime_type,storage_path,sha256,size_bytes,status,parse_quality,metadata_json,created_at,updated_at,indexed_at|runtime-machine-local
runtime-db|runtime_image_annotations|id,user_id,image_asset_id,annotation_kind,content_text,content_hash,source_model,status,metadata_json,created_at,updated_at|runtime-machine-local
runtime-db|runtime_image_assets|id,user_id,filename,mime_type,storage_path,sha256,size_bytes,width,height,status,retention_state,metadata_json,created_at,updated_at,indexed_at|runtime-machine-local
runtime-db|runtime_image_message_links|id,user_id,message_id,image_asset_id,attachment_id,created_at|runtime-machine-local
runtime-db|runtime_knowledge_bundle_runs|id,user_id,run_type,status,source_id,input_json,result_json,error_json,started_at,completed_at,created_at|runtime-machine-local
runtime-db|runtime_knowledge_concept_sources|id,user_id,concept_id,source_id,span_id,citation_label,quote_text,metadata_json,created_at|runtime-machine-local
runtime-db|runtime_knowledge_concepts|id,user_id,concept_type,slug,title,description,body_markdown,frontmatter_json,metadata_json,content_hash,status,created_at,updated_at,compiled_at|runtime-machine-local
runtime-db|runtime_knowledge_links|id,user_id,source_concept_id,target_concept_id,link_type,confidence,metadata_json,created_at,updated_at|runtime-machine-local
runtime-db|runtime_messages|id,thread_id,user_id,run_id,step_id,sequence_id,role,content_text,content_json,tool_name,tool_call_id,tool_args_json,is_in_context,is_archived_history,token_estimate,source,created_at|runtime-machine-local
runtime-db|runtime_reembed_completions|user_id,completed,updated_at|runtime-machine-local
runtime-db|runtime_runs|id,thread_id,user_id,provider,model,mode,status,stop_reason,error_text,started_at,completed_at,prompt_tokens,completion_tokens,total_tokens,pending_approval_message_id|runtime-machine-local
runtime-db|runtime_session_notes|id,thread_id,user_id,key,value,note_type,is_active,promoted_to_item_id,created_at|runtime-machine-local
runtime-db|runtime_source_artifacts|id,user_id,source_id,artifact_kind,content_text,content_hash,metadata_json,created_at,updated_at|runtime-machine-local
runtime-db|runtime_source_spans|id,user_id,source_id,artifact_id,span_kind,locator_json,locator_hash,content_text,content_hash,metadata_json,created_at,updated_at|runtime-machine-local
runtime-db|runtime_sources|id,user_id,kind,source_uri,content_hash,title,media_type,status,metadata_json,created_at,updated_at,indexed_at|runtime-machine-local
runtime-db|runtime_steps|id,run_id,thread_id,step_index,status,request_json,response_json,tool_calls_json,usage_json,error_text,created_at|runtime-machine-local
runtime-db|runtime_threads|id,user_id,status,title,created_at,updated_at,last_message_at,closed_at,is_archived,archive_retry_count,archive_next_retry_at,archive_failed,next_message_sequence|runtime-machine-local
runtime-db|runtime_workflow_checkpoints|id,workflow_run_id,checkpoint_index,state_name,status,input_json,output_json,artifact_refs_json,idempotency_key,error_json,created_at|runtime-machine-local
runtime-db|runtime_workflow_runs|id,user_id,thread_id,workflow_type,status,current_state,input_json,result_json,error_json,retry_count,max_retries,created_at,updated_at,started_at,completed_at|runtime-machine-local
runtime-db|working_context|id,user_id,section,content,version,updated_by,updated_at|runtime-machine-local
server-config|runtime-config|agent_provider,agent_model,agent_persona_template,agent_base_url,agent_extraction_model,agent_extraction_provider,agent_embedding_provider,agent_embedding_model,agent_embedding_base_url|device-runtime-config
server-config|runtime-config|agent_api_key,agent_api_keys_json,agent_embedding_api_key|os-credential
browser-local|keys|anima-theme,anima-background-config,anima-translate-lang,anima_ascii_settings,anima_clock_format,anima_dashboard_node_positions,anima_dashboard_closed_nodes,anima_bgm_muted,anima_bgm_state|corefs-preferences
browser-local|keys|anima_nav_collapsed,anima-sidebar-collapsed,anima-agent-rail-collapsed,anima-show-trace,anima-debug-db-viewer,db-query-draft,db-bookmarks,db-col-widths,db-hidden-columns,db-last-session,db-query-history,db-recent-tables,db-saved-queries,db-table-preferences,anima-mod-url,anima:cloud-providers-enabled,anima:key-hint:*,anima_last_user,anima-background-media-device,anima_bgm_device_tracks|device-ui-config
browser-local|keys|anima_user|remove-private-profile-cache
browser-local|keys|anima_unlock_token|remove-legacy-session
browser-local|keys|anima_daemon_control_token,ANIMA_DAEMON_CONTROL_TOKEN|os-credential
browser-local|keys|legacy-journal-draft:*|corefs-object
browser-local|keys|anima:diary:draft-migration-state:v1:*|device-migration-state
browser-session|keys|anima_unlock_token,anima_dashboard_greeting,anima_dashboard_greeting_oneshot,anima_pending_recovery,anima_today_context|session-only
app-data|files|legacy:.anima/runtime-config.json|remove-after-device-config-migration
app-data|files|runtime-config.json|device-runtime-config
app-data|files|legacy:users/<id>/soul.md|remove-after-soul-migration
app-data|files|runtime-daemon.control-token|os-credential
app-data|files|runtime-daemon.state.json,runtime-port,runtime-lock,runtime-logs|device-runtime-state
app-data|files|legacy-draft-cleanup-v1.lock,legacy-draft-cleanup-v1.epoch.json|device-migration-state
app-data|files|core-instance-registry.json,.core-instance-registry.lock,.core-instance-registry.guard|device-instance-registry
app-data|files|integration-links.json|device-integration-registry
app-data|files|regeneration.json|device-runtime-state
app-data|files|corefs-client-access.json|device-client-grants
anima-mod-sqlite|mod_config|mod_id,key,value,is_secret,updated_at|device-mod-config-or-credential-reference
anima-mod-sqlite|mod_state|mod_id,enabled,status,last_error,started_at,updated_at|device-runtime-state
anima-mod-sqlite|mod_events|id,mod_id,event_type,detail,created_at|device-runtime-state
anima-mod-sqlite|mod_store|namespace,key,value,updated_at|device-mod-state
anima-mod-store|google:tokens:*|accessToken,refreshToken,expiresAt,email|os-credential
```
<!-- portable-state-inventory:v1:end -->

## Browser storage

| Store | Key or key family | Destination |
|---|---|---|
| localStorage | `anima-theme` | CoreFS preferences |
| localStorage | `anima-background-config` | CoreFS preferences; imported media becomes a Core attachment while host paths remain device-local |
| localStorage | `anima-translate-lang` | CoreFS preferences |
| localStorage | `anima_ascii_settings` | CoreFS preferences |
| localStorage | `anima_clock_format` | CoreFS preferences |
| localStorage | `anima_dashboard_node_positions` | CoreFS preferences |
| localStorage | `anima_dashboard_closed_nodes` | CoreFS preferences |
| localStorage | `anima_bgm_muted`, `anima_bgm_state` | CoreFS preferences for bundled media; custom host paths are device-local |
| localStorage | `anima-background-media-device`, `anima_bgm_device_tracks` | Device-local references/metadata for host media that was not explicitly imported into CoreFS |
| localStorage | `anima_nav_collapsed`, `anima-sidebar-collapsed`, `anima-agent-rail-collapsed` | Device-local viewport state |
| localStorage | `anima-show-trace`, `anima-debug-db-viewer`, `db-query-draft`, `db-bookmarks`, `db-col-widths`, `db-hidden-columns`, `db-last-session`, `db-query-history`, `db-recent-tables`, `db-saved-queries`, `db-table-preferences` | Device-local developer state |
| localStorage | `anima-mod-url`, `anima:cloud-providers-enabled` | Device-local topology/security choice |
| localStorage | `anima:key-hint:*` | Device-local derived display hint; removable |
| localStorage | `anima_last_user` | Optional device-local convenience; never required for unlock |
| localStorage | `anima_user` | Remove private profile cache; unlocked profile is session memory only |
| localStorage | `anima_unlock_token` | Remove legacy copy; token is session/process only |
| localStorage | `anima_daemon_control_token`, `ANIMA_DAEMON_CONTROL_TOKEN` | Migrate to OS credential and scrub |
| localStorage | legacy Journal draft keys | Migrate to encrypted CoreFS draft objects and scrub under the PCF-004 cleanup authority |
| localStorage | `anima:diary:draft-migration-state:v1:*` | Non-sensitive device-local migration sidecar; retain until authorized source cleanup |
| sessionStorage | `anima_unlock_token` | Session-only and cleared on lock/logout |
| sessionStorage | `anima_dashboard_greeting`, `anima_dashboard_greeting_oneshot` | Session-only, user-bound decrypted cache |
| sessionStorage | `anima_pending_recovery` | Session-only setup handoff; clear after acknowledgement/lock |
| sessionStorage | `anima_today_context` | Session-only same-day interaction context |

Generic browser persistence helpers must not introduce a key outside this
table. Dynamic Journal draft keys are the only private legacy localStorage
family and are migration inputs, never a permitted new writer destination.

## Persisted server settings

The checked persisted whitelist is `_PERSISTED_RUNTIME_SETTING_FIELDS` in
`apps/server/src/anima_server/config.py`.

| Fields | Destination |
|---|---|
| `agent_provider`, `agent_model`, `agent_persona_template`, `agent_base_url`, `agent_extraction_model`, `agent_extraction_provider`, `agent_embedding_provider`, `agent_embedding_model`, `agent_embedding_base_url` | Device-local runtime config outside `.anima/` |
| `agent_api_key`, `agent_api_keys_json`, `agent_embedding_api_key` | OS credentials; scrub from legacy runtime-config after verified import |

No other `Settings` field is persisted by this application-managed file;
environment and launch configuration remain machine-local inputs.

## Application-data and legacy files

| Producer | Path/family | Destination |
|---|---|---|
| server | legacy `.anima/runtime-config.json` | Copy-verify-delete into platform app data; secret fields go to OS credentials |
| server | platform `anima/<instance>/config/runtime-config.json` | Device-local runtime config with credential references only |
| server | legacy `users/<id>/soul.md` | Soul `user_directive`/persona section, then verified deletion |
| local daemon/Tauri | `runtime-daemon.control-token` and browser copies | Shared OS credential entry, then verified deletion |
| local daemon | `runtime-daemon.state.json`, runtime port/lock/log files | Device-local operational state |
| Tauri | `legacy-draft-cleanup-v1.lock`, `legacy-draft-cleanup-v1.epoch.json` | Device-local cleanup authority state; contains no private draft body/hash |
| server | `core-instance-registry.json` plus lock/guard files | Device-local Core/instance binding and runtime-engine migration state |
| server | instance `config/integration-links.json` | Device-local Telegram/Discord link registry; relink after transfer |
| server | instance `work/regeneration.json` | Disposable machine-local regeneration work state |
| server | instance `config/corefs-client-access.json` | Device-local verified installation identities, folder grants, generations, and audit timestamps; never transferred with the Core |
| CoreFS runtime | instance registry, migration journal, index/checkpoint files | Device-local authenticated metadata outside portable content |

## anima-mod stores

| Record/value | Destination |
|---|---|
| `mod_config` non-secret fields | Device-local mod configuration |
| `mod_config` fields whose schema type is `secret` | Opaque OS-credential reference only; legacy plaintext value is imported and scrubbed |
| `mod_state`, `mod_events` | Device-local operational state; event detail must not contain secrets |
| `mod_store` ordinary namespaced values | Device-local mod state |
| `google:tokens:*` OAuth values | Dedicated OS credential/broker secret store; scrub from `mod_store` |
| legacy YAML secret fields | Import to OS credential references and scrub/rewrite the YAML |

## Credential boundary

Provider keys are written/read only by the server credential service. The
desktop and local daemon share only the daemon-control credential through
Tauri/native commands. anima-mod receives short-lived, audience-scoped broker
capabilities and stores opaque references; it has no generic browser secret-read
route. Google OAuth access/refresh tokens use the dedicated secret-store path.
Every boundary fails closed if its secure platform backend is unavailable.
