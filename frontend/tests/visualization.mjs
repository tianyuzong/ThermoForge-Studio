import assert from 'node:assert/strict';
import {suggestedDeformation, deformationCycle, updateDeformedPositions} from '../src/visualization.ts';

assert.equal(deformationCycle(0), 0);
assert.equal(deformationCycle(2), 1);
assert.equal(deformationCycle(4), 0);
for(let t=0;t<20;t+=.03) assert(deformationCycle(t)>=0 && deformationCycle(t)<=1);
const rest = new Float32Array([0,0,0,1,2,3]);
const displacement = new Float32Array([0,0,0,.1,-.2,.3]);
const before = Array.from(displacement), target = new Float32Array(6);
updateDeformedPositions(target,rest,displacement,0);
assert.deepEqual(target,rest);
updateDeformedPositions(target,rest,displacement,717);
updateDeformedPositions(target,rest,displacement,1);
Array.from(target).forEach((v,i)=>assert(Math.abs(v-[0,0,0,1.1,1.8,3.3][i])<1e-6));
for(const t of [0,1,2,3,4]) {
  updateDeformedPositions(target,rest,displacement,deformationCycle(t));
  Array.from(target).forEach((v,i)=>assert(Math.abs(v-(rest[i]+displacement[i]*deformationCycle(t)))<1e-6));
}
updateDeformedPositions(target,rest,displacement,10);
Array.from(target).forEach((v,i)=>assert(Math.abs(v-[0,0,0,2,0,6][i])<1e-6));
assert.deepEqual(Array.from(displacement),before);
assert.equal(suggestedDeformation([[0,0,0],[1,0,0]],[[0,0,0],[.001,0,0]]),120);
assert.equal(suggestedDeformation([[0,0,0],[1,0,0]],[[0,0,0],[0,0,0]]),1);
assert.equal(suggestedDeformation([[0,0,0],[1,0,0]],[[0,0,0],[1e-9,0,0]]),1000);
console.log('PASS: static animation endpoints, displacement interpolation, zero field and original data preservation.');
