const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx={window:{}};vm.createContext(ctx);vm.runInContext(fs.readFileSync('light_studio/static/customization.js','utf8'),ctx);
const api=ctx.window.FilamentLooks;
const themes=[{name:'aurora',palette:{water:[20,50,80],rest:[20,30,40],collect:[80,150,200]}}];
const look={version:1,name:'Evening',settings:{theme:'aurora',printing_motion:'comet',palette_overrides:{water:[230,80,170]},brightness:50,speed:1,ripples:true,reduced_motion:false,quiet:false,waterline_marks:true}};
const valid=api.validate(look,themes);valid.settings.palette_overrides.water[0]=1;assert.equal(look.settings.palette_overrides.water[0],230);
for(const change of [{theme:'missing'},{brightness:NaN},{speed:'1'},{quiet:1},{printing_motion:'strobe'},{palette_overrides:{water:[true,1,2]}},{palette_overrides:{extra:[1,2,3]}},{slots:[]}])assert.throws(()=>api.validate({...look,settings:{...look.settings,...change}},themes));
for(const obj of [{...look,name:''},{...look,name:'x'.repeat(41)},{...look,version:2},{...look,url:'http://example.test'}])assert.throws(()=>api.validate(obj,themes));
const p={water:[10,20,30],rest:[100,100,100],collect:[150,160,170]};const before=JSON.stringify(p);const out=api.resolve(p,{water:[220,60,140],rest:[60,30,90]});assert.equal(JSON.stringify(p),before);assert.deepEqual(JSON.parse(JSON.stringify(out.deep)),[13,7,20]);
console.log('14 look validation, copying and palette checks pass');
// Search covers the card's own words and its category, not just the blurb.
const searchable=[{label:'Candy',category:'Neon',blurb:'Bubblegum, grape and a lemon-sherbet finish.'},
                  {label:'Classic',category:'Minimal',blurb:'One hue per meaning: orange water over deep blue.',summary:'Orange progress, blue remainder, green finish and red alerts.'}];
assert.equal(searchable.filter(t=>api.matches(t,'neon')).length,1);
assert.equal(searchable.filter(t=>api.matches(t,'remainder')).length,1);
assert.equal(searchable.filter(t=>api.matches(t,'CANDY')).length,1);
assert.equal(searchable.filter(t=>api.matches(t,'  minimal  ')).length,1);
assert.equal(searchable.filter(t=>api.matches(t,'')).length,2);
assert.equal(searchable.filter(t=>api.matches(t,'nothing-here')).length,0);
console.log('6 theme search checks pass');
