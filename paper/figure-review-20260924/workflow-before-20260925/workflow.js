// All positions derive from this shared four-column grid and local card spacing.
const L = { margin: 24, gap: 32, card: 204, inset: 14, line: 26,
  measureY: 30, measureH: 142, serveY: 350, serveH: 136,
  budgetY: 542, budgetH: 64, compileY: 658, compileH: 146 };
const col = i => L.margin + i * (L.card + L.gap);
const W = 2 * L.margin + 4 * L.card + 3 * L.gap;
const H = L.compileY + L.compileH + 100;
const C = { ink: '#303740', muted: '#606C77', line: '#CDD5DC',
  blue: '#376F9B', blueBg: '#EFF5F9', green: '#2D7569', greenBg: '#EDF6F1',
  amber: '#93661F', amberBg: '#FBF4E6', red: '#AB514B', redBg: '#FAEFED',
  purple: '#705AA0', purpleBg: '#F3EFF8', grayBg: '#F6F7F9' };
const svg = d3.select('#chart').append('svg').attr('xmlns','http://www.w3.org/2000/svg')
  .attr('width',W).attr('height',H).attr('viewBox',`0 0 ${W} ${H}`)
  .attr('data-figure','pace-workflow-review-v1').style('font-family','"Source Sans 3", Arial, sans-serif');
svg.append('rect').attr('width',W).attr('height',H).attr('fill','#fff').attr('data-layer','background');
const defs = svg.append('defs');
for (const [name, paths] of Object.entries(ICONS)) {
  defs.append('g').attr('id',`i-${name}`).attr('fill','none').attr('stroke','currentColor')
    .attr('stroke-width',1.65).attr('stroke-linecap','round').attr('stroke-linejoin','round').html(paths);
}
for (const k of ['ink','blue','green','red','purple','amber']) {
  defs.append('marker').attr('id',`a-${k}`).attr('viewBox','0 0 10 8').attr('refX',9).attr('refY',4)
    .attr('markerWidth',6).attr('markerHeight',5).attr('orient','auto')
    .append('path').attr('d','M0,0 L10,4 L0,8 Z').attr('fill',C[k]);
}
function rect(g,x,y,w,h,fill,stroke,key,r=7) {
  return g.append('rect').attr('x',x).attr('y',y).attr('width',w).attr('height',h)
    .attr('rx',r).attr('fill',fill).attr('stroke',stroke).attr('stroke-width',1.25).attr('data-node',key);
}
function label(g,x,y,s,size=21,weight=400,color=C.ink,anchor='start') {
  return g.append('text').attr('x',x).attr('y',y).attr('font-size',size).attr('font-weight',weight)
    .attr('fill',color).attr('text-anchor',anchor).attr('data-label',s).text(s);
}
function icon(g,name,x,y,size=28,color=C.ink) {
  const node=g.append('g').attr('transform',`translate(${x},${y}) scale(${size/24})`)
    .attr('color',color).attr('data-icon',name);
  node.append('use').attr('href',`#i-${name}`);
  return node;
}
function arrow(points,key,color='ink',dash=false) {
  return svg.append('path').attr('d',d3.line()(points)).attr('fill','none').attr('stroke',C[color])
    .attr('stroke-width',1.5).attr('stroke-dasharray',dash?'5 4':null)
    .attr('marker-end',`url(#a-${color})`).attr('data-edge',key);
}
function card(i,y,h,key,color='blue') {
  const g=svg.append('g').attr('transform',`translate(${col(i)},${y})`).attr('data-node',key);
  rect(g,0,0,L.card,h,C[color+'Bg'],C[color],key+'-frame');
  return g;
}
function title(g,name,s,color='blue') {
  icon(g,name,L.inset,L.inset,29,C[color]);
  label(g,L.inset+39,L.inset+23,s,23,600,C[color]);
}
function connect(i,y,key,color='ink') {
  arrow([[col(i)+L.card+2,y],[col(i+1)-4,y]],key,color);
}
function region(y,h,key) {
  rect(svg,L.margin/2,y,W-L.margin,h,'#fff',C.line,key,10);
}
// Small extruded cards depict reusable artifacts, keeping flow arrows flat.
function artifact(g,x,y,w,h,symbol,key,color='green') {
  const d=L.inset/3;
  g.append('path').attr('d',`M${x},${y} l${d},${-d} h${w} v${h} l${-d},${d} Z`)
    .attr('fill',color==='green'?'#CFE2D9':'#D4E2ED').attr('stroke',C[color]).attr('stroke-width',1)
    .attr('data-node',key+'-depth');
  rect(g,x,y,w,h,'#fff',C[color],key+'-face',3);
  icon(g,symbol,x+(w-26)/2,y+(h-26)/2,26,C[color]);
}

// (a) Measurement: separate obtaining a program from serving with it.
region(L.margin/2,256,'measurement');
let g=card(0,L.measureY,L.measureH,'source-traces');
title(g,'files','Agent traces');
const stackX=L.inset+7, stackY=4*L.inset+1;
for (let i=2;i>=0;i--) artifact(g,stackX+i*7,stackY-i*5,43,49,'clipboard-list',`trace-${i}`,'blue');
label(g,stackX+68,stackY+15,'k completed',21);
label(g,stackX+68,stackY+41,'runs',21);
label(g,L.inset,L.measureH-L.inset,'Service charged once',19,400,C.muted);

g=card(1,L.measureY,L.measureH,'compile-and-validate');
title(g,'code-xml','Compile');
label(g,L.inset,4*L.inset+12,'Build → validate',22,600);
icon(g,'refresh-cw',L.inset,6*L.inset+1,24,C.blue);
label(g,L.inset+33,6*L.inset+20,'Bounded repair',20);
icon(g,'circle-check',L.inset,L.measureH-31,21,C.green);
icon(g,'circle-x',L.inset+29,L.measureH-31,21,C.red);
label(g,L.inset+62,L.measureH-L.inset,'Keep both costs',19,400,C.amber);

g=card(2,L.measureY,L.measureH,'fresh-deployment');
title(g,'clipboard-list','Deploy');
label(g,L.inset,4*L.inset+8,'Fresh inputs',22,600);
icon(g,'brain',L.inset,5*L.inset+7,27,C.green);
label(g,L.inset+37,5*L.inset+29,'Extract',21);
artifact(g,L.card-59,5*L.inset+5,38,32,'code-xml','deployed-program');
g.append('path').attr('d',`M${L.card-92},${5*L.inset+21} H${L.card-66}`)
  .attr('stroke',C.green).attr('marker-end','url(#a-green)').attr('data-edge','extract-to-program');
label(g,L.inset,L.measureH-L.inset,'Cost + task success',20);

g=card(3,L.measureY,L.measureH,'measured-profile');
title(g,'chart-column','Cost profile');
['Agent / program use','Pass / fail attempts','Program failures'].forEach((s,i)=>
  label(g,L.inset,4*L.inset+9+i*L.line,s,20));
for(let i=0;i<3;i++) connect(i,L.measureY+L.measureH/2,`measure-${i}`,'blue');
icon(svg,'circle-check',col(2)-L.gap/2-10,L.measureY+L.measureH/2-32,20,C.green);

const checkY=L.measureY+L.measureH+18, checkH=38;
rect(svg,col(1),checkY,L.card,checkH,'#fff',C.line,'repeat-check');
icon(svg,'refresh-cw',col(1)+L.inset,checkY+7,24,C.blue);
label(svg,col(1)+L.inset+34,checkY+26,'Repeat attempts',20);
rect(svg,col(2),checkY,L.card,checkH,'#fff',C.line,'matched-check');
icon(svg,'bot',col(2)+L.inset,checkY+7,24,C.blue);
label(svg,col(2)+L.inset+34,checkY+26,'Matched replays',20);
const noteY=checkY+checkH+25;
label(svg,W/2,noteY,'Record every compilation charge; compare serving on matched inputs.',20,400,C.amber,'middle');
label(svg,W/2,noteY+38,'(a) Measurement',24,600,C.ink,'middle');

// (b) Online protocol: serving precedes any compilation decision.
const onlineY=L.serveY-48;
region(onlineY,H-onlineY-45,'online-protocol');
label(svg,L.margin,L.serveY-17,'1  Serve the current task',23,600);

g=card(0,L.serveY,L.serveH,'task-arrives','gray');
// Gray cards use neutral borders.
g.select('rect').attr('stroke',C.line);
title(g,'calendar','Task arrives','ink');
label(g,L.inset,5*L.inset,'Update reference',21);
label(g,L.inset,5*L.inset+L.line,'Aₜ = Aₜ₋₁ + aₜ',24,600);

g=card(1,L.serveY,L.serveH,'service-budget','purple');
title(g,'shield','Service check','purple');
label(g,L.inset,5*L.inset,'Reserve routing,',21);
label(g,L.inset,5*L.inset+L.line,'service + fallback',21);

g=card(2,L.serveY,L.serveH,'serve-task','green');
title(g,'bot','Serve task','green');
icon(g,'bot',L.inset,4*L.inset+2,24);
label(g,L.inset+34,4*L.inset+22,'Agent',21,600);
label(g,L.card-L.inset,4*L.inset+22,'or',19,400,C.muted,'end');
icon(g,'brain',L.inset,6*L.inset+4,25,C.green);
label(g,L.inset+33,6*L.inset+24,'Extract',20,400,C.green);
icon(g,'code-xml',L.card-42,6*L.inset+4,27,C.green);
g.append('path').attr('d',`M${L.card-84},${6*L.inset+17} H${L.card-48}`)
  .attr('stroke',C.green).attr('marker-end','url(#a-green)').attr('data-edge','online-extract-to-program');

g=card(3,L.serveY,L.serveH,'record-service','amber');
title(g,'table-2','Record cost','amber');
label(g,L.inset,5*L.inset,'Charge actual use',21);
label(g,L.inset,5*L.inset+L.line,'Retain agent traces',21);
for(let i=0;i<3;i++) connect(i,L.serveY+L.serveH/2,`service-${i}`);

const serviceNoteY=L.serveY+L.serveH+26;
label(svg,col(1),serviceNoteY,'No budget: clear manifest → agent',19,400,C.red);
label(svg,col(3)+L.card,serviceNoteY,'Detected failure → agent fallback',19,400,C.red,'end');

// Both purple checks use the same observed-arrival budget.
rect(svg,L.margin,L.budgetY,W-2*L.margin,L.budgetH,C.purpleBg,C.purple,'shared-budget');
icon(svg,'shield',L.margin+L.inset,L.budgetY+18,30,C.purple);
label(svg,L.margin+L.inset+42,L.budgetY+27,'Shared cumulative budget',23,600,C.purple);
label(svg,L.margin+L.inset+42,L.budgetY+51,'Routing + service + all compilation attempts',20,400,C.ink);
label(svg,W-L.margin-L.inset,L.budgetY+28,'Kₜ ≤ (1 + ε) Aₜ',27,600,C.purple,'end');
label(svg,W-L.margin-L.inset,L.budgetY+51,'Agent reference, observed arrivals only',19,400,C.muted,'end');

label(svg,L.margin,L.compileY-18,'2  After service: consider compilation for later tasks',23,600);
g=card(0,L.compileY,L.compileH,'eligibility','gray');
g.select('rect').attr('stroke',C.line);
title(g,'files','Eligible?','ink');
label(g,L.inset,5*L.inset,'k agent traces',22,600);
label(g,L.inset,5*L.inset+L.line,'No live program',21);

g=card(1,L.compileY,L.compileH,'price-aware-proposal','green');
title(g,'chart-column','Price test','green');
label(g,L.inset,4*L.inset+9,'Estimated savings',21,600);
label(g,L.inset,4*L.inset+9+L.line,'> price + routing',21,600);
label(g,L.inset,L.compileH-L.inset,'Counts failed attempts',19,400,C.muted);

g=card(2,L.compileY,L.compileH,'compilation-budget','purple');
title(g,'shield','Compile check','purple');
label(g,L.inset,5*L.inset,'Reserve either',21);
label(g,L.inset,5*L.inset+L.line,'outcome',21);

g=card(3,L.compileY,L.compileH,'compile-and-verify','green');
title(g,'code-xml','Compile','green');
icon(g,'circle-check',L.inset,4*L.inset+4,24,C.green);
label(g,L.inset+33,4*L.inset+24,'Store program',21,600,C.green);
icon(g,'circle-x',L.inset,6*L.inset+5,24,C.red);
label(g,L.inset+33,6*L.inset+25,'No program',21,400,C.red);
label(g,L.inset,L.compileH-L.inset,'Charge pass or fail',20,600,C.amber);
for(let i=0;i<3;i++) connect(i,L.compileY+L.compileH/2,`compilation-${i}`);

// The saved artifact is reusable only on subsequent arrivals.
const rail=W-L.margin/2-5;
arrow([[col(3)+L.card,L.compileY+L.compileH/2],[rail,L.compileY+L.compileH/2],
  [rail,L.serveY-5],[col(0)+L.card/2,L.serveY-5],[col(0)+L.card/2,L.serveY-1]],'program-for-later-tasks','green');
label(svg,col(3)+L.card,L.serveY-17,'Later tasks',19,600,C.green,'end');
label(svg,col(0),L.compileY+L.compileH+28,'Any test fails: defer',19,400,C.muted);
arrow([[col(1)+L.card/2,L.compileY+L.compileH+26],[col(1)+L.card/2,L.compileY+L.compileH+3]],'evidence-to-price','blue');
label(svg,col(1)+L.card/2+16,L.compileY+L.compileH+28,'Measured costs + past arrivals + outcomes',19,400,C.blue);
label(svg,W/2,H-15,'(b) Online compilation protocol',24,600,C.ink,'middle');
