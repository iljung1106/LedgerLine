import type { StudioModel } from "./types";

export const PREPARE_IDS_MESSAGE = "Structural editing is locked because this project has no persistent event IDs. Run ledgerline prepare-ids after reviewing its dry-run and backup; listening and inspector pitch, velocity, and articulation edits remain available.";

export function structuralEditingAvailable(model: StudioModel): boolean {
  return model.project.prepared_ids === true
    && model.capabilities.structural_editing !== false
    && model.capabilities.move_within_measure !== false
    && model.capabilities.resize_with_validation !== false;
}
