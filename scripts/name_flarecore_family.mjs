import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const family=new Set(['FlareRender','FlarePresetLoader','FlareDepthAdapter','FlareKeyframes','FlareElementPrompts','FlareTexturePrepare','FlareElementSave','FlareGeneratorSelect']);
function update(graph){
 for(const node of graph.nodes || []){
  if(!family.has(node.type))continue;
  // This registry attribution came from another extension importing the
  // global node map. Remove it; do not invent a registry ID for this pack.
  if(node.properties?.cnr_id==='comfyui_fearnworksnodes'){
   delete node.properties.cnr_id;delete node.properties.ver;
  }
  if(node.type==='FlareRender' && !node.title)node.title='Flarecore · Render';
  if(node.type==='FlareElementPrompts' && ['01 · Define element','element prompt',undefined].includes(node.title))node.title='Flarecore · Element Forge';
 }
 for(const sub of graph.definitions?.subgraphs || [])update(sub);
}
for(const name of fs.readdirSync(path.join(root,'example_workflows')).filter(n=>n.endsWith('.json'))){
 const file=path.join(root,'example_workflows',name),before=fs.readFileSync(file,'utf8'),doc=JSON.parse(before);
 const original=JSON.stringify(doc);update(doc);
 if(JSON.stringify(doc)!==original){fs.writeFileSync(file,JSON.stringify(doc,null,2)+'\n');console.log(name);}
}
