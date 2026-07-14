// SPDX-License-Identifier: Apache-2.0

mod parser;
mod planner;

pub use parser::{
    parse_patch, Patch, PatchChunk, PatchError, PatchOperation, PatchPath, MAX_PATCH_BYTES,
    MAX_PATCH_OPERATIONS,
};
pub use planner::{plan_patch, PatchPlan, PatchSnapshot, PlannedMutation};
