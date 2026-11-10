import type {ReplayPreferences} from './replayDisplay';
// The live desk uses the profile already selected on the deployed site. Hidden
// per-browser research choices must not make phones show a different live desk.
export const ACTIVE_PROFILE:{model:'current'|'expanded';protection:'none'|'trailing7'}={model:'expanded',protection:'none'};
export function liveReplayPreferences(stored:ReplayPreferences):ReplayPreferences{
 return {...stored,model:ACTIVE_PROFILE.model,path:'combined',protect7:ACTIVE_PROFILE.protection==='trailing7'};
}
