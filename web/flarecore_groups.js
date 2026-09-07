// SPDX-License-Identifier: Apache-2.0
export const SOURCE_FIELDS = new Set([
 'position_mode','light_x','light_y','flare_x','flare_y','detect_threshold','detect_max_lights',
 'occlusion_radius','light_depth','invert_depth','seed','occlusion_smooth','scene_color',
 'track_smoothing','track_max_jump','depth_normalize','depth_blur','depth_temporal_smooth',
 'light_path','mask_falloff','light_travel','track_points','track_feature','track_search',
 'track_hold','track_fade','search_radius','scene_lock','anchor_path','visibility_mode','use_lights_input'
]);
export const clone = value => JSON.parse(JSON.stringify(value));
export function sceneDocument(node) {
 try {return JSON.parse(node.widgets?.find(w=>w.name==='preset_json')?.value || '{}');}
 catch {return null;}
}
export function activeGroup(document) {
 if(!Array.isArray(document?.groups))return null;
 return document.groups.find(g=>g?.id===document.active_group) || document.groups[0] || null;
}
export function sourceValue(node,name) {
 if(!SOURCE_FIELDS.has(name))return undefined;
 return activeGroup(sceneDocument(node))?.source?.[name];
}
export function setGroupSource(node,name,value) {
 if(!SOURCE_FIELDS.has(name) || !activeGroup(sceneDocument(node)))return false;
 node._fcEditor?.flushPending();
 const document=sceneDocument(node),group=activeGroup(document);
 group.source ||= {};group.source[name]=value;
 if(node._fcEditor)node._fcEditor.writeDocument(document,{quiet:true});
 else node.widgets.find(w=>w.name==='preset_json').value=JSON.stringify(document);
 node.setDirtyCanvas?.(true,false);return true;
}
export function capturedSource(node) {
 const source={};
 for(const widget of node.widgets || [])if(SOURCE_FIELDS.has(widget.name))source[widget.name]=widget.value;
 source.use_lights_input=!!node.inputs?.some(input=>input.name==='lights' && input.link!=null);
 return source;
}
export function ensureGroups(document,node) {
 if(Array.isArray(document.groups) && document.groups.length)return document;
 const preset=clone(document);
 preset.schema_version ||= 1;preset.elements ||= [];preset.global ||= {};
 return {schema_version:1,name:'Flare scene',global:{},elements:[],active_group:'flare_1',
   groups:[{id:'flare_1',name:'Flare 1',enabled:true,preset,source:capturedSource(node)}]};
}
export function newGroupId(document) {
 let n=1;while(document.groups.some(g=>g.id===`flare_${n}`))n++;
 return `flare_${n}`;
}
export function selectGroupResult(node,document) {
 const group=activeGroup(document),result=node._fcGroupResults?.[group?.id];
 node._fcTrack=result?.track || [];
 node._fcLightSrc=result?.source || group?.source?.position_mode || 'manual';
 node._fcSourceStatus=result?.status || 'This group has not been rendered yet.';
 node._fcPicker?.draw();
}
