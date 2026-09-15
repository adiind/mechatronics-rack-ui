/* Appearance-only browser library. No printer assignments or hardware writes. */
'use strict';
window.FilamentLooks = (() => {
  const STORAGE='filament-wall.looks.v1', LIMIT=24;
  const roles=[['water','Printed portion'],['rest','Unprinted portion'],['idle','Available'],['prep','Preparing'],['pause','Paused'],['error','Error'],['collect','Ready to collect'],['stopped','Stopped early'],['offline','Offline'],['unknown','Unknown']];
  const keys=['brightness','speed','ripples','reduced_motion','quiet','waterline_marks','theme','palette_overrides','printing_motion'];
  let api,root,dialog,category='All',query='',looks=[],removed=null;
  const $=id=>document.getElementById(id);
  const copy=x=>JSON.parse(JSON.stringify(x));
  const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;};
  const hex=c=>'#'+c.map(x=>x.toString(16).padStart(2,'0')).join('');
  const rgb=h=>[1,3,5].map(i=>parseInt(h.slice(i,i+2),16));
  const blend=(a,b,k)=>a.map((v,i)=>Math.round(v*(1-k)+b[i]*k));
  function resolve(p,o={}) {
    const out={...p,...o},derived={};
    if(o.water)Object.assign(derived,{drop:blend(out.water,out.collect,.65),splash:blend(out.water,out.collect,.8),ident_body:out.water,ripple_other:out.water});
    if(o.rest)derived.deep=blend(out.rest,[0,0,0],.78);
    if(o.prep)Object.assign(derived,{prep_low:blend(out.prep,[0,0,0],.85),prep_high:blend(out.prep,[0,0,0],.6)});
    for(const k of ['pause','error'])if(o[k])derived[k+'_mark']=blend(out[k],[255,255,255],.65);
    if(o.collect)Object.assign(derived,{ripple_complete:out.collect,ident_core:out.collect});
    for(const[k,v]of Object.entries(derived))if(!(k in o))out[k]=v;
    return out;
  }
  /* Search what the card actually shows, plus the category it is filed under:
     "neon" used to find nothing while the Neon category held four themes. */
  const haystack=t=>[t.label,t.category,t.summary,t.blurb].filter(Boolean).join(' ').toLowerCase();
  function matches(t,q){return haystack(t).includes(String(q||'').trim().toLowerCase());}
  function validate(value,themes) {
    if(!value||value.version!==1||typeof value.name!=='string'||!value.name.trim()||value.name.trim().length>40||Object.keys(value).some(k=>!['version','name','settings'].includes(k)))throw Error('Use a look file with a name of 1–40 characters.');
    const s=value.settings;
    if(!s||typeof s!=='object'||Array.isArray(s)||Object.keys(s).some(k=>!keys.includes(k))||keys.some(k=>!(k in s)))throw Error('This look has missing or unsupported settings.');
    if(!themes.some(t=>t.name===s.theme)||!['rain','flow','comet','still'].includes(s.printing_motion))throw Error('Unknown theme or printing motion.');
    for(const[k,min,max]of [['brightness',0,100],['speed',.25,2]])if(typeof s[k]!=='number'||!Number.isFinite(s[k])||s[k]<min||s[k]>max)throw Error('Brightness or speed is out of range.');
    for(const k of ['ripples','reduced_motion','quiet','waterline_marks'])if(typeof s[k]!=='boolean')throw Error('Invalid look toggle.');
    const o=s.palette_overrides,allowed=Object.keys(themes[0].palette).filter(k=>!k.startsWith('hue_'));
    if(!o||typeof o!=='object'||Array.isArray(o))throw Error('Invalid custom colours.');
    for(const[k,c]of Object.entries(o))if(!allowed.includes(k)||!Array.isArray(c)||c.length!==3||c.some(n=>!Number.isInteger(n)||n<0||n>255))throw Error('Custom colours need three channels from 0 to 255.');
    return {version:1,name:value.name.trim(),settings:copy(s)};
  }
  function message(text){$('looks-message').textContent=text;}
  function persist(next){try{localStorage.setItem(STORAGE,JSON.stringify(next));looks=next;renderLooks();return true;}catch(e){message('This browser could not save the look. Copy its JSON to keep it.');return false;}}
  function change(patch){api.onChange({...copy(api.getSettings()),...patch});}
  function theme(){return api.themes.find(t=>t.name===api.getSettings().theme)||api.themes[0];}
  function gallery(){
    const host=$('theme-picker');host.replaceChildren();
    const found=api.themes.filter(t=>(category==='All'||t.category===category)&&matches(t,query));
    $('theme-results').textContent=found.length+(found.length===1?' theme':' themes');
    for(const t of found){
      const b=el('button','theme-card');b.type='button';b.dataset.theme=t.name;b.setAttribute('aria-pressed',String(t.name===api.getSettings().theme));
      const strip=el('span','theme-strip');for(const k of ['idle','prep','water','rest','pause','error','collect','stopped']){const sw=el('i');sw.style.background=hex(t.swatches[k]);strip.append(sw);}
      b.append(strip,el('strong','theme-name',t.label),el('span','theme-category',t.category),el('span','theme-description',t.summary||t.blurb));
      b.addEventListener('click',()=>{change({theme:t.name,palette_overrides:{}});dialog.close();api.notify(t.label+' staged in the preview. Save & apply when ready.');});host.append(b);
    }
    if(!found.length)host.append(el('p','empty-result','No themes match. Try another name or category.'));
  }
  function renderLooks(){
    const host=$('saved-looks');host.replaceChildren();
    if(!looks.length)host.append(el('p','fineprint','Your saved looks will appear here.'));
    looks.forEach((look,index)=>{const row=el('div','saved-look');const load=el('button','saved-look-load',look.name);load.title='Load '+look.name+' into the preview';load.onclick=()=>{api.onChange(copy(look.settings));message(look.name+' loaded as a draft.');};
      const rename=el('button','text','Rename');rename.setAttribute('aria-label','Rename '+look.name);rename.onclick=()=>{const next=prompt('Rename look',look.name);if(next===null)return;try{const v=validate({...look,name:next},api.themes);if(looks.some((x,i)=>i!==index&&x.name.toLowerCase()===v.name.toLowerCase()))throw Error('That name is already used.');const all=copy(looks);all[index]=v;persist(all);}catch(e){message(e.message);}};
      const remove=el('button','text','Remove');remove.setAttribute('aria-label','Remove '+look.name);remove.onclick=()=>{if(persist(looks.filter((_,i)=>i!==index))){removed={look,index};$('restore-look').hidden=false;message('Look removed. Undo is available below.');}};
      row.append(load,rename,remove);host.append(row);});
  }
  function mount(config){
    api=config;root=$('look-controls');if(!root)return;
    root.innerHTML=`<div class="look-summary"><span class="label">Current draft</span><strong id="look-name"></strong><div class="theme-strip" id="look-swatches"></div><button type="button" id="browse-themes" class="primary wide">Browse ${api.themes.length} themes</button></div>
      <label class="motion-label" for="printing-motion">Printing motion<select id="printing-motion"><option value="rain">Rain · falling drops</option><option value="flow">Flow · gentle glow</option><option value="comet">Comet · travelling light</option><option value="still">Still · solid progress</option></select></label><p class="fineprint">Changes decoration while printing. Progress and alert signals stay meaningful.</p>
      <details class="look-section"><summary>Make the colours yours <span id="custom-count"></span></summary><p class="fineprint">Adjust each state. Highlights and shadows follow your colours.</p><div id="custom-colours"></div><button id="reset-colours" class="text" type="button">Reset all colours to theme</button></details>
      <details class="look-section"><summary>My looks <span class="tag">this browser</span></summary><p class="fineprint">Save theme, colours, motion and wall settings here. Loading creates a draft; printer assignments and caps stay as they are.</p><label for="look-save-name">Look name</label><div class="look-save-row"><input id="look-save-name" maxlength="40" placeholder="e.g. Quiet evening"><button id="save-look" class="secondary" type="button">Save look</button></div><div id="saved-looks"></div><button id="restore-look" class="text" type="button" hidden>Undo removed look</button>
      <details class="look-transfer"><summary>Import / export look</summary><p class="fineprint">Copy a look between browsers as JSON. Import adds it to My looks.</p><textarea id="look-json" aria-label="Look JSON" rows="5" maxlength="32768" placeholder="Paste look JSON here"></textarea><div class="look-save-row"><button id="export-look" class="secondary" type="button">Export current draft</button><button id="import-look" class="secondary" type="button">Import look</button></div></details><p id="looks-message" class="fineprint" role="status"></p></details>`;
    if(dialog)dialog.remove();query='';category='All';
    dialog=el('dialog','theme-gallery');dialog.setAttribute('aria-labelledby','gallery-title');
    dialog.innerHTML=`<div class="gallery-head"><div><p class="label">Find your wall’s personality</p><h2 id="gallery-title">Theme library</h2></div><button class="secondary" id="close-gallery" aria-label="Close theme library">Close ×</button></div><p class="fineprint">Choose a palette to preview. This resets custom colours; nothing reaches the wall until you save.</p><label for="theme-search" class="sr-only">Search themes</label><input id="theme-search" type="search" placeholder="Search themes"><div id="theme-categories" class="theme-categories" role="group" aria-label="Theme categories"></div><p class="fineprint" id="theme-results" aria-live="polite"></p><div id="theme-picker" class="theme-picker" aria-label="Colour themes"></div>`;
    document.body.append(dialog);
    for(const cat of ['All',...new Set(api.themes.map(t=>t.category))]){const b=el('button','secondary',cat);b.type='button';b.setAttribute('aria-pressed',String(cat==='All'));b.onclick=()=>{category=cat;for(const c of $('theme-categories').children)c.setAttribute('aria-pressed',String(c===b));gallery();};$('theme-categories').append(b);}
    $('browse-themes').onclick=()=>{gallery();dialog.showModal();$('theme-search').focus();};$('close-gallery').onclick=()=>dialog.close();
    $('theme-search').oninput=()=>{query=$('theme-search').value;gallery();};
    $('printing-motion').onchange=()=>change({printing_motion:$('printing-motion').value});
    for(const[key,label]of roles){const row=el('div','colour-row');const l=el('label','',label);l.htmlFor='colour-'+key;const input=el('input');input.id='colour-'+key;input.type='color';input.setAttribute('aria-label',label+' colour');const output=el('input');output.type='text';output.maxLength=7;output.id='hex-'+key;output.setAttribute('aria-label',label+' hex colour');output.onchange=()=>{if(!/^#[0-9a-f]{6}$/i.test(output.value)){api.notify('Use a six-digit hex colour, such as #54FFD2.');sync(api.getSettings());return;}change({palette_overrides:{...(api.getSettings().palette_overrides||{}),[key]:rgb(output.value)}});};output.oninput=()=>{if(/^#[0-9a-f]{6}$/i.test(output.value))output.onchange();};output.onkeydown=e=>{if(e.key==='Enter')output.blur();};
      input.oninput=()=>change({palette_overrides:{...(api.getSettings().palette_overrides||{}),[key]:rgb(input.value)}});
      const reset=el('button','text','Reset');reset.type='button';reset.setAttribute('aria-label','Reset '+label+' colour');reset.onclick=()=>{const o={...api.getSettings().palette_overrides};delete o[key];change({palette_overrides:o});};row.append(l,input,output,reset);$('custom-colours').append(row);}
    $('reset-colours').onclick=()=>change({palette_overrides:{}});
    $('save-look').onclick=()=>{try{if(looks.length>=LIMIT)throw Error('Keep up to 24 looks. Remove one to make room.');const look=validate({version:1,name:$('look-save-name').value,settings:api.getSettings()},api.themes);if(looks.some(x=>x.name.toLowerCase()===look.name.toLowerCase()))throw Error('Choose a different name; that look already exists.');if(persist([...looks,look])){message('Saved in this browser. The physical wall has not changed.');$('look-save-name').value='';}}catch(e){message(e.message);}};
    $('restore-look').onclick=()=>{if(!removed)return;if(looks.length>=LIMIT||looks.some(x=>x.name.toLowerCase()===removed.look.name.toLowerCase()))return message('Free a slot or rename the matching look before restoring.');const a=[...looks];a.splice(removed.index,0,removed.look);if(persist(a)){removed=null;$('restore-look').hidden=true;message('Look restored.');}};
    $('export-look').onclick=()=>{try{$('look-json').value=JSON.stringify(validate({version:1,name:$('look-save-name').value.trim()||'My wall look',settings:api.getSettings()},api.themes),null,2);message('Copy the JSON below to another browser.');}catch(e){message(e.message);}};
    $('import-look').onclick=()=>{try{if($('look-json').value.length>32768)throw Error('Look file is too large.');if(looks.length>=LIMIT)throw Error('Keep up to 24 looks.');const look=validate(JSON.parse($('look-json').value),api.themes);if(looks.some(x=>x.name.toLowerCase()===look.name.toLowerCase()))throw Error('A look with that name already exists. Rename it in the JSON first.');if(persist([...looks,look]))message('Imported '+look.name+'. Load it to preview.');}catch(e){message(e instanceof SyntaxError?'That is not valid JSON.':e.message);}};
    try{const raw=localStorage.getItem(STORAGE);if(raw&&raw.length>786432)throw Error();const data=JSON.parse(raw||'[]');if(!Array.isArray(data)||data.length>LIMIT)throw Error();looks=data.map(x=>validate(x,api.themes));}catch(e){looks=[];message('Saved looks could not be read. Export your next look for a backup.');}
    renderLooks();sync(api.getSettings());
  }
  function sync(settings){if(!root||!api)return;const t=theme(),o=settings.palette_overrides||{},p=resolve(t.palette,o);
    $('look-name').textContent=t.label+(Object.keys(o).length?' · custom':'');$('printing-motion').value=settings.printing_motion||'rain';$('custom-count').textContent=Object.keys(o).length?' · '+Object.keys(o).length+' edited':'';
    const strip=$('look-swatches');strip.replaceChildren();for(const k of ['water','rest','idle','pause','error','collect']){const i=el('i');i.style.background=hex(p[k]);strip.append(i);}
    for(const[key]of roles){$('colour-'+key).value=hex(p[key]);if(document.activeElement!==$('hex-'+key))$('hex-'+key).value=hex(p[key]).toUpperCase();}
    if(dialog.open)gallery();
  }
  return {mount,sync,resolve,validate,matches};
})();
