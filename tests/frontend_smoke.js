/* Lightweight DOM contract smoke test, not a browser or visual-layout test.
 * Invoked by python -m unittest discover -s tests -v when Node.js is available. */
const fs=require('fs'),vm=require('vm'),path=require('path');
const root=path.resolve(__dirname,'..');
class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.attrs={};this.events={};this.style={};this.scrollTop=0;this.scrollLeft=0;this.hidden=false;this.value='';this.className='';this.open=false;this.classList={add:()=>{},remove:()=>{}};}
 append(...nodes){this.children.push(...nodes)} replaceChildren(...nodes){this.children=[...nodes]}
 setAttribute(k,v){this.attrs[k]=String(v)} getAttribute(k){return this.attrs[k]}
 addEventListener(k,v){this.events[k]=v} querySelectorAll(){return []} querySelector(){return null}
 showModal(){this.open=true} close(){this.open=false;this.events.close?.({})} focus(){} scrollIntoView(){} remove(){} select(){}
 set textContent(v){this.children=[String(v)]} get textContent(){return this.children.map(n=>n instanceof Element?n.textContent:String(n)).join('')}
}
const html=fs.readFileSync(path.join(root,'app/static/index.html'),'utf8'),source=fs.readFileSync(path.join(root,'app/static/app.js'),'utf8');
const nodes={}; for(const m of html.matchAll(/id="([^"]+)"/g)){if(nodes[m[1]])throw Error('Duplicate HTML ID '+m[1]);nodes[m[1]]=new Element();}
for(const m of source.matchAll(/\$\('([^']+)'\)/g)){if(!nodes[m[1]])throw Error('Missing static HTML ID '+m[1]);}
const document={getElementById:id=>nodes[id]||(nodes[id]=new Element()),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),createTextNode:t=>String(t),querySelectorAll:()=>[],querySelector:()=>null,addEventListener:()=>{},body:new Element(),hidden:false};
const ctx={document,Node:Element,URL,console,setTimeout:()=>1,clearTimeout:()=>{},Date,Map,Set,JSON,Object,Array,String,Number,Promise,window:{isSecureContext:false},navigator:{},CSS:{escape:String},localStorage:{setItem:()=>{}}};
vm.createContext(ctx); vm.runInContext(source.replace(/  init\(\);\n\}\)\(\);\s*$/, '  globalThis.__test = {state, renderTask, renderDetail, renderEntries, el, matches};\n})();'),ctx);
const task=JSON.parse(fs.readFileSync(process.argv[2],'utf8')); const api=ctx.__test;api.state.task=task; api.state.tasks=[task];
for(const entry of task.entries){ api.state.selected=entry.id; api.renderTask(); for(const tab of ['comparison','evidence','original','queries']){api.state.tab=tab;api.renderDetail();if(!nodes['entry-detail'].textContent)throw Error('Empty detail '+tab);}}
api.state.query='';for(const filter of ['all','issues','conflict','missing_doi','upgrade','insufficient','failed','chinese']){api.state.filter=filter;api.renderEntries()}
const a=api.el('button',{'aria-selected':true,'aria-pressed':false});if(a.attrs['aria-selected']!=='true'||a.attrs['aria-pressed']!=='false')throw Error('ARIA state invalid');
console.log(JSON.stringify({entries:task.entries.length,tabsChecked:task.entries.length*4,filtersChecked:8,aria:'valid',rendering:'passed'}));

const unverified={id:'unverified',fields:{doi:'10.1000/unverified',title:'Unverified DOI'},result:{doi_state:'unverified',identity_state:'unique',field_state:'consistent'}};
api.state.filter='missing_doi';if(!api.matches(unverified))throw Error('Unverified DOI missing from DOI filter');
api.state.filter='insufficient';if(!api.matches(unverified))throw Error('Unverified DOI missing from evidence filter');
api.state.task.entries=[unverified];api.state.selected='unverified';api.state.tab='comparison';api.renderDetail();if(!nodes['entry-detail'].textContent.includes('未核验'))throw Error('Unverified DOI label missing');
console.log('Unverified DOI labels and filters passed.');

unverified.result.calls=[{provider:'llm',status:'ok',tokens:{prompt_tokens:10,completion_tokens:5}},{provider:'cnki',status:'no_results',tokens:{}},{provider:'cnki',operation:'fetch',status:'error',tokens:{}}];
api.state.tab='queries';api.renderDetail();const trace=nodes['entry-detail'].textContent;
for(const text of ['15 tokens','公开网页检索未命中','候选待核验']){if(!trace.includes(text))throw Error('Missing query display: '+text)}
if(trace.includes('[object Object]'))throw Error('Unrendered token object');
console.log('Token usage and CNKI query labels passed.');
