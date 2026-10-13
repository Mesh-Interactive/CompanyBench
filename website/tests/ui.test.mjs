import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import * as dataModule from '../dist/data.js';

// Exercise the real app handlers without a browser or network. Startup loading
// stays pending; tests supply the frozen fixture and a small DOM substitute.
class Element {
  constructor(dataset={},rect={left:100,right:120,top:200,height:20,width:20}) {
    this.dataset=dataset; this.rect=rect; this.attributes=new Map();
    this.hidden=true; this.open=false; this.isConnected=true; this.style={};
    this.classList={contains:()=>false};
  }
  addEventListener() {}
  closest() { return this; }
  contains(element) { return element===this; }
  matches(selector) { return selector===':popover-open' && this.open; }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  setAttribute(name,value) { this.attributes.set(name,value); }
  removeAttribute(name) { this.attributes.delete(name); }
  getBoundingClientRect() { return this.rect; }
  showPopover() { this.open=true; }
  hidePopover() { this.open=false; }
}

function app() {
  const elements=new Map([['hover-tooltip',new Element({}, {width:320,height:180})]]);
  const handlers=new Map(),timers=new Map(); let nextTimer=0;
  const document={
    getElementById(id) { if (!elements.has(id)) elements.set(id,new Element()); return elements.get(id); },
    addEventListener(event,handler) { if (!handlers.has(event)) handlers.set(event,[]); handlers.get(event).push(handler); },
    querySelectorAll() { return []; },
  };
  const fixture=dataModule.normalizeDataset(JSON.parse(fs.readFileSync(new URL('../dist/data/demo.json',import.meta.url))));
  const source=fs.readFileSync(new URL('../dist/app.js',import.meta.url),'utf8');
  const imports=[...source.matchAll(/^import \{([^}]+)\} from '.\/data\.js';$/gm)];
  assert.equal(imports.length,1);
  const context=vm.createContext({document,fixture,
    moduleData:{...dataModule,loadData:()=>new Promise(()=>{})},
    window:{innerWidth:1280,innerHeight:720,addEventListener() {}},
    setTimeout(fn,delay) { const id=++nextTimer; timers.set(id,{fn,delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(source.replace(imports[0][0],`const {${imports[0][1]}}=moduleData;`)+
    '\ndata=fixture; filtered=data.queries; summaries=summarize(data); globalThis.ui={tooltipPosition,tooltipContent,showTooltip,hideTooltip};',context);
  return {ui:context.ui,tooltip:elements.get('hover-tooltip'),timers,document,
    fire(name,event={}) { for (const handler of handlers.get(name) ?? []) handler(event); },
    advance(delay) { for (const [id,timer] of [...timers]) if (timer.delay<=delay) {timers.delete(id);timer.fn();} },
  };
}

test('tooltip placement flips at the right edge and stays within small viewports',()=>{
  const {ui}=app();
  const size={width:270,height:180},viewport={width:390,height:844};
  const right=ui.tooltipPosition({left:365,right:380,top:760,height:20},size,viewport);
  assert.equal(right.left,85);
  assert.equal(right.top,652);
  const left=ui.tooltipPosition({left:2,right:12,top:-10,height:12},size,viewport);
  assert.equal(left.left,22);
  assert.equal(left.top,12);
  assert.ok(right.left+size.width<=viewport.width-12);
  assert.ok(right.top+size.height<=viewport.height-12);
});

test('tooltips require actual pointer movement, delay opening and dismiss on leave',()=>{
  const api=app(),point=new Element({point:'avina'});
  api.fire('pointermove',{target:point,pointerType:'mouse',movementX:0,movementY:0});
  assert.equal(api.timers.size,0);
  api.fire('pointermove',{target:point,pointerType:'mouse',movementX:1,movementY:0});
  api.advance(199);
  assert.equal(api.tooltip.hidden,true);
  api.advance(200);
  assert.equal(api.tooltip.hidden,false);
  assert.match(api.tooltip.innerHTML,/Quality score/);
  assert.match(api.tooltip.innerHTML,/87\.2/);
  assert.equal(point.getAttribute('aria-describedby'),'hover-tooltip');
  api.fire('pointerout',{target:point,relatedTarget:null});
  api.advance(100);
  assert.equal(api.tooltip.hidden,true);
  assert.equal(point.getAttribute('aria-describedby'),null);
});

test('leaving before the delay cancels opening and the tooltip remains readable on hover',()=>{
  const api=app(),point=new Element({point:'avina'});
  api.fire('pointermove',{target:point,pointerType:'mouse',movementX:1,movementY:0});
  api.fire('pointerout',{target:point,relatedTarget:null});
  api.advance(200);
  assert.equal(api.tooltip.hidden,true);
  api.ui.showTooltip(point);
  api.fire('pointerout',{target:point,relatedTarget:api.tooltip});
  api.advance(100);
  assert.equal(api.tooltip.hidden,false);
});

test('scrolling long tooltip content keeps it open, while scrolling the page dismisses it',()=>{
  const api=app(),point=new Element({point:'avina'});
  api.ui.showTooltip(point);
  api.fire('scroll',{target:api.tooltip});
  assert.equal(api.tooltip.hidden,false);
  api.fire('scroll',{target:api.document});
  assert.equal(api.tooltip.hidden,true);
});

test('keyboard focus shows a tooltip, Escape dismisses it and pointer clicks do not cause focus flashes',()=>{
  const api=app(),point=new Element({point:'avina'});
  api.fire('keydown',{key:'Tab',target:point});
  api.fire('focusin',{target:point});
  assert.equal(api.tooltip.hidden,false);
  api.document.activeElement=point;
  api.fire('pointerout',{target:point,relatedTarget:null});
  api.advance(100);
  assert.equal(api.tooltip.hidden,false);
  let prevented=false;
  api.fire('keydown',{key:'Escape',target:point,preventDefault(){prevented=true;},stopPropagation(){}});
  assert.equal(api.tooltip.hidden,true);
  assert.equal(prevented,true);
  api.fire('pointerdown',{target:point,pointerType:'mouse'});
  api.fire('focusin',{target:point});
  assert.equal(api.tooltip.hidden,true);
});

test('tooltip source text is escaped and result cells use their actual query metrics',()=>{
  const {ui}=app();
  const text=ui.tooltipContent(new Element({tooltip:'<img src=x onerror=alert(1)>'}));
  assert.ok(!text.includes('<img'));
  assert.match(text,/&lt;img/);
  const result=ui.tooltipContent(new Element({query:'CSB-001',resultProvider:'avina'}));
  assert.match(result,/CSB-001/);
  assert.match(result,/Precision/);
  assert.ok(!result.includes(' per query'));
  assert.match(ui.tooltipContent(new Element({helpMetric:'quality_score'})),/200 ×/);
});
