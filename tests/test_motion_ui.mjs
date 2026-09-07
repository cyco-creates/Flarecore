import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sampleCurve, validatePoints, responseProfile} from '../web/flarecore_motion.js';
const fixtures=JSON.parse(readFileSync(new URL('../docs/motion_previews/curve_fixtures.json',import.meta.url)));
const contract=JSON.parse(readFileSync(new URL('../docs/motion_previews/motion_contract.json',import.meta.url)));
for (const f of fixtures) assert.ok(Math.abs(sampleCurve(f.points,f.x,f.mode)-f.expected)<1e-12);
assert.throws(()=>validatePoints([[0,1],[0,2]],contract.targets.opacity));
assert.throws(()=>validatePoints([[0,1],[1,NaN]],contract.targets.opacity));
assert.throws(()=>validatePoints([[0,1],[1,9]],contract.targets.opacity));
for(const kind of ['glow','iris','ring','hoop','streak','texture','orbs','spectral']) {
  for(const name of ['breathe','edge','ghost']) {
    const p=responseProfile(name,kind);
    for (const c of p.channels) {
      validatePoints(c.points,contract.targets[c.target]);
      assert.ok(contract.drivers[c.driver]);
      assert.ok(!contract.targets[c.target].types || contract.targets[c.target].types.includes(kind));
    }
  }
}
console.log('20 Python/JS curve parity cases, input guards and 24 profile/type combinations passed.');
