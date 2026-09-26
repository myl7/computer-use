// Four aligned columns and three flow rows share one spacing grid.
const L = { margin:24, gap:32, card:244, pad:18, icon:28, iconGap:18,
  measureY:54, measureH:400, serveY:620, serveH:310,
  budgetY:994, budgetH:68, compileY:1218, compileH:214 };
const col=i=>L.margin+i*(L.card+L.gap);
const W=2*L.margin+4*L.card+3*L.gap, H=L.compileY+L.compileH+148;
// Reuse the approved teaser palette and symbol definitions.
const S=FIGURE_STYLE, P=S.colors;
const C={ink:P.ink,muted:P.muted,line:P.line,
  blue:P.ink,blueBg:P.stripBg,green:P.prog,greenBg:P.progFill,
  amber:P.cost,amberBg:P.costFill,red:P.fail,redBg:P.failFill,
  purple:P.cost,purpleBg:P.costFill,gray:P.ink,grayBg:P.inkFill};
const svg=d3.select('#chart').append('svg').attr('xmlns','http://www.w3.org/2000/svg')
  .attr('width',W).attr('height',H).attr('viewBox',`0 0 ${W} ${H}`)
  .attr('data-figure','pace-workflow-review-v2').style('font-family',S.font);
svg.append('rect').attr('width',W).attr('height',H).attr('fill','#fff').attr('data-layer','background');
const defs=svg.append('defs');
for(const [name,paths] of Object.entries(ICONS)) defs.append('g').attr('id',`i-${name}`)
  .attr('fill','none').attr('stroke','currentColor').attr('stroke-width',S.iconStroke)
  .attr('stroke-linecap','round').attr('stroke-linejoin','round').html(paths);
for(const k of ['ink','blue','green','red','purple','amber','gray']) defs.append('marker')
  .attr('id',`a-${k}`).attr('viewBox','0 0 10 8').attr('refX',9).attr('refY',4)
  .attr('markerWidth',5.5).attr('markerHeight',4.5).attr('orient','auto')
  .append('path').attr('d','M0,0 L10,4 L0,8 Z').attr('fill',C[k]);
function rect(g,x,y,w,h,fill,stroke,key,r=S.nodeRadius){return g.append('rect').attr('x',x).attr('y',y)
  .attr('width',w).attr('height',h).attr('rx',r).attr('fill',fill).attr('stroke',stroke)
  .attr('stroke-width',S.boxStroke).attr('data-node',key);}
function label(g,x,y,s,size=23,weight=400,color=C.ink,anchor='start'){return g.append('text')
  .attr('x',x).attr('y',y).attr('font-size',size).attr('font-weight',weight).attr('fill',color)
  .attr('text-anchor',anchor).attr('data-label',s).text(s);}
function icon(g,name,x,y,size=L.icon,color=C.ink){if(name===S.icons.modelCall)color=C.ink;const n=g.append('g')
  .attr('transform',`translate(${x},${y}) scale(${size/24})`).attr('color',color).attr('data-icon',name);
  n.append('use').attr('href',`#i-${name}`);return n;}
function arrow(g,points,key,color='ink',headed=true){return g.append('path').attr('d',d3.line()(points))
  .attr('fill','none').attr('stroke',C[color]).attr('stroke-width',1.4)
  .attr('marker-end',headed?`url(#a-${color})`:null).attr('data-edge',key);}
// Small semantic accents follow the teaser's panel headers.
const CARD_ACCENTS={
  'source-traces':P.ink,'compile-and-verify':P.cost,'matched-serving':P.prog,
  'task-arrives':P.ink,'service-budget':P.cost,'serve-task':P.prog,'record-service':P.cost,
  'eligibility':P.ink,'cost-aware-proposal':P.prog,'compilation-budget':P.cost
};
function card(i,y,h,key,color='blue',w=L.card){
  const accent=CARD_ACCENTS[key]||P.ink;
  const g=svg.append('g').attr('transform',`translate(${col(i)},${y})`).attr('data-card',key).attr('data-accent',accent);
  rect(g,0,0,w,h,P.stripBg,P.line,key+'-frame',10);
  rect(g,0,0,w,4,accent,'none',key+'-accent',2);
  return g;
}
function title(g,name,s,color='blue'){
  const width=Number(g.select('rect').attr('width'));
  label(g,width/2,L.pad+24,s,23,600,g.attr('data-accent')||C.ink,'middle');
}
function node(g,y,h,s,key,color='blue',name=null,w=L.card-2*L.pad){
  const x=w<=L.card-2*L.pad?(L.card-w)/2:L.pad;
  rect(g,x,y,w,h,color==='green'?P.progFill:color==='red'?P.failFill:'#fff',(color==='blue'||color==='gray')?P.line:C[color],key);
  const lines=Array.isArray(s)?s:[s];
  const textX=name?x+L.pad+L.icon+L.iconGap:x+w/2;
  if(name) icon(g,name,x+L.pad,y+(h-L.icon)/2,L.icon,C[color]);
  lines.forEach((line,i)=>label(g,textX,y+h/2+8+(i-(lines.length-1)/2)*26,
    line,22,400,C[color],name?'start':'middle'));
}
function region(y,h,key){rect(svg,L.margin/2,y,W-L.margin,h,'#fff',C.line,key,10);}
function link(i,y,key,color='ink'){arrow(svg,[[col(i)+L.card+2,y],[col(i+1)-4,y]],key,color);}
function artifact(g,x,y,w,h,key){const d=5;g.append('path').attr('d',`M${x},${y} l${d},${-d} h${w} v${h} l${-d},${d} Z`)
  .attr('fill','#E4E8EC').attr('stroke',C.blue).attr('data-node',key+'-depth');
  rect(g,x,y,w,h,'#fff',C.blue,key,3);icon(g,'clipboard-list',x+(w-28)/2,y+(h-28)/2,28,C.blue);}


if(PART !== 'online') {
// Measurement: input, compilation, paired service, and measured outputs.
{
let g=card(0,L.measureY,L.measureH,'source-traces');title(g,'files','Source traces');
const traceY=80, traceX=2*L.pad, extractY=180, verifyY=280, buildY=traceY-8, buildH=64;
const narrow=L.card-3*L.pad,narrowX=(L.card-narrow)/2;
rect(g,L.pad,traceY-24,L.card-2*L.pad,96,'#fff',C.line,'three-source-traces');
for(let i=2;i>=0;i--) artifact(g,traceX+i*6,traceY-i*5,43,49,`trace-${i}`);
label(g,traceX+76,traceY+15,'3 agent',24,600);label(g,traceX+76,traceY+44,'traces',24,600);
arrow(g,[[L.card/2,traceY+72],[L.card/2,extractY-4]],'traces-to-binding-model','blue');
node(g,extractY,64,['Binding','extraction'],'binding-extraction','blue','bot');
arrow(g,[[L.card/2,extractY+64],[L.card/2,verifyY-4]],'model-to-bindings','blue');
node(g,verifyY,48,'Bindings','verification-bindings','blue');

g=card(1,L.measureY,L.measureH,'compile-and-verify');title(g,'code-xml','Compile');
node(g,buildY,buildH,['Generate','program'],'generate-program','blue','bot',narrow);
arrow(g,[[narrowX+narrow/2,buildY+buildH],[narrowX+narrow/2,verifyY-4]],'program-to-verification','blue');
label(g,narrowX+narrow/2-12,(buildY+buildH+verifyY)/2+8,'Program',21,400,C.blue,'end');
node(g,verifyY,48,'Verify','verification','blue',null,narrow);
const repairX=L.card-L.pad/2;
arrow(g,[[narrowX+narrow,verifyY+24],[repairX,verifyY+24],[repairX,buildY+buildH/2],[narrowX+narrow+3,buildY+buildH/2]],'bounded-repair','red');
label(g,repairX-10,(verifyY+buildY)/2+22,'Bounded repair',19,400,C.red,'middle')
  .attr('transform',`rotate(-90,${repairX-10},${(verifyY+buildY)/2+22})`);
arrow(svg,[[col(0)+L.card-L.pad,L.measureY+traceY+24],[col(1)+narrowX-3,L.measureY+buildY+buildH/2]],'traces-to-code','blue');
arrow(svg,[[col(0)+L.card-L.pad,L.measureY+verifyY+24],[col(1)+narrowX-3,L.measureY+verifyY+24]],'bindings-to-verify','blue');
// This scope mark identifies what is repeated without implying new source runs.
const scopeLeft=col(0)+L.pad,scopeRight=col(1)+L.card-L.pad,scopeY=L.measureY-14;
svg.append('path').attr('d',`M${scopeLeft},${scopeY+7}V${scopeY}H${scopeRight}V${scopeY+7}`)
  .attr('fill','none').attr('stroke',C.blue).attr('data-edge','repeat-compilation-scope');
label(svg,(scopeLeft+scopeRight)/2,scopeY-12,'Repeat compilation from these traces',22,400,C.blue,'middle');

// Task prompts feed both serving paths. Only the program path extracts inputs.
const compareW=L.card+64, promptY=74, extractTaskY=140, runY=206, agentY=280;
g=card(2,L.measureY,L.measureH,'matched-serving','blue',compareW);title(g,'clipboard-list','Serving comparison');
const innerW=compareW-2*L.pad;
node(g,promptY,40,'Task prompt','fresh-task-prompt','blue',null,innerW);
node(g,extractTaskY,44,'Binding extraction','deployment-extraction','green','bot',innerW);
node(g,runY,44,'Program execution','program-service','green',null,innerW);
node(g,agentY,44,'Agent execution','matched-agent-run','blue',null,innerW);
const mid=compareW/2, promptRail=compareW-L.pad/2;
arrow(g,[[mid,promptY+40],[mid,extractTaskY-3]],'prompt-to-extraction','blue');
arrow(g,[[mid,extractTaskY+44],[mid,runY-3]],'extracted-inputs-to-program','green');
arrow(g,[[compareW-L.pad,promptY+20],[promptRail,promptY+20],[promptRail,agentY+22],
  [compareW-L.pad+3,agentY+22]],'same-prompt-to-agent','blue');
const outcomeY=L.measureH-35;
rect(g,L.pad,outcomeY-18,innerW,36,'#fff',C.line,'serving-outcome');
label(g,compareW/2,outcomeY+8,'Cost + task success',22,400,C.ink,'middle');
const outputRail=L.pad/2;
// Branches merge without arrowheads. One shared arrow enters the outcome node.
arrow(g,[[mid,runY+44],[mid,runY+58],[outputRail,runY+58],[outputRail,outcomeY]],'program-to-outcome-junction','green',false);
arrow(g,[[L.pad,agentY+22],[outputRail,agentY+22]],'agent-to-outcome-junction','blue',false);
arrow(g,[[outputRail,outcomeY],[L.pad-3,outcomeY]],'serving-outcomes-merge','ink');
const passRail=col(2)-L.gap/2;
arrow(svg,[[col(1)+narrowX+narrow,L.measureY+verifyY+24],[passRail,L.measureY+verifyY+24],
  [passRail,L.measureY+runY+22],[col(2)+L.pad-3,L.measureY+runY+22]],'verified-program-to-serving','green');
const passY=L.measureY+(verifyY+runY)/2+24;
label(svg,passRail-7,passY,'Pass',19,400,C.green,'middle').attr('transform',`rotate(-90,${passRail-7},${passY})`);

// A report-shaped output separates metrics from executable steps.
const resultX=col(3)+64,resultW=L.card-64;
g=svg.append('g').attr('transform',`translate(${resultX},${L.measureY})`).attr('data-card','measurement-results');
const fold=22;
g.append('path').attr('d',`M0,0 H${resultW-fold} L${resultW},${fold} V${L.measureH} H0 Z`)
  .attr('fill',C.amberBg).attr('stroke',C.amber).attr('stroke-width',1.3).attr('data-node','metrics-report');
g.append('path').attr('d',`M${resultW-fold},0 V${fold} H${resultW}`).attr('fill','none').attr('stroke',C.amber).attr('data-node','report-fold');
label(g,resultW/2,38,'MEASURED',17,600,C.amber,'middle');
label(g,resultW/2,70,'Results',27,600,C.amber,'middle');
[['Compilation','cost'],['Per-use','saving'],['Payback','count']].forEach((lines,i)=>{
  const y=108+i*88;
  if(i) g.append('path').attr('d',`M${L.pad},${y-26}H${resultW-L.pad}`).attr('stroke','#DFD2BB').attr('data-node',`result-divider-${i}`);
  lines.forEach((t,j)=>label(g,resultW/2,y+j*28,t,23,400,C.ink,'middle'));
});
arrow(svg,[[col(2)+compareW-L.pad,L.measureY+outcomeY],[resultX-3,L.measureY+outcomeY]],'serving-to-results','blue');
const ledgerY=L.measureY+L.measureH+L.pad,ledgerH=44;
rect(svg,col(1),ledgerY,L.card,ledgerH,C.amberBg,C.amber,'attempt-cost-record');
label(svg,col(1)+L.card/2,ledgerY+31,'Compilation costs',22,400,C.amber,'middle');
const costX=col(1)+narrowX+narrow/2;
arrow(svg,[[costX,L.measureY+verifyY+48],[costX,ledgerY-3]],'all-outcomes-record-cost','amber');
arrow(svg,[[col(1)+L.card,ledgerY+ledgerH/2],[resultX+resultW/2,ledgerY+ledgerH/2],
  [resultX+resultW/2,L.measureY+L.measureH+3]],'attempt-costs-to-results','amber');
}
}
if(PART !== 'measurement') {
// Online algorithm, stage 1: service branches are explicit.
{
let g;
const onlineTop=L.serveY-44;

label(svg,L.margin+L.pad,L.serveY-12,'1  Serve the current task',25,600);
g=card(0,L.serveY,L.serveH,'task-arrives','gray');title(g,'calendar','Task arrives','gray');
label(g,L.pad,5*L.pad,'Update reference',23);
label(g,L.pad,7*L.pad,'Aₜ = Aₜ₋₁ + aₜ',28,600);
node(g,9*L.pad,48,'Task prompt','arriving-task-prompt','gray');
label(g,L.card/2,L.serveH-L.pad-25,'Agent execution',21,400,C.muted,'middle');
label(g,L.card/2,L.serveH-L.pad,'reference',21,400,C.muted,'middle');

g=card(1,L.serveY,L.serveH,'service-budget','purple');title(g,'shield','Service budget','purple');
label(g,L.pad,5*L.pad,'Reserve routing,',23);
label(g,L.pad,5*L.pad+30,'service + fallback',23);
node(g,L.serveH-66,44,'Clear manifest','clear-manifest','red');
arrow(g,[[L.card/2,5*L.pad+36],[L.card/2,L.serveH-69]],'service-budget-no','red');
label(g,L.card/2+14,L.serveH-76,'No',19,400,C.red);

g=card(2,L.serveY,L.serveH,'serve-task','green');title(g,'bot','Serve task','green');
const chooseY=64, programY=132, agentServeY=244, choiceW=L.card-2*L.pad;
node(g,chooseY,40,'Live program?','program-available','green',null,choiceW);
rect(g,L.pad,programY,L.card-2*L.pad,72,'#fff',C.green,'online-program-service');
icon(g,'bot',2*L.pad,programY+8,24,C.green);
label(g,2*L.pad+24+L.iconGap,programY+28,'Extract bindings',19,400,C.green);
label(g,L.card/2,programY+60,'Program execution',21,400,C.green,'middle');
node(g,agentServeY,44,'Agent execution','online-agent-service','blue');
const noRail=L.pad/2;
arrow(g,[[L.card/2,chooseY+40],[L.card/2,programY-3]],'program-available-yes','green');
label(g,L.card/2+14,programY-10,'Yes',18,400,C.green);
arrow(g,[[L.card/2-52,chooseY+40],[L.card/2-52,chooseY+52],[noRail,chooseY+52],[noRail,agentServeY-18],
  [L.card/2-52,agentServeY-18],[L.card/2-52,agentServeY-3]],'no-program-use-agent','blue');
label(g,L.pad+2,agentServeY-26,'No',18,400,C.blue);
arrow(g,[[L.card/2,programY+76],[L.card/2,agentServeY-3]],'detected-failure-to-agent','red');
label(g,L.card/2+12,agentServeY-10,'Detected failure',15,400,C.red);
const resultRail=L.card-L.pad/2, resultY=L.serveH/2;
// A headless junction prevents two arrowheads at the service output.
arrow(g,[[L.card-L.pad,programY+36],[resultRail,programY+36],[resultRail,resultY]],'program-service-complete','ink',false);
arrow(g,[[L.card-L.pad,agentServeY+22],[resultRail,agentServeY+22],[resultRail,resultY]],'agent-service-complete','ink',false);

g=card(3,L.serveY,L.serveH,'record-service','amber');title(g,'table-2','Record','amber');
label(g,L.pad,5*L.pad,'Charge actual cost',23,600);
label(g,L.pad,7*L.pad,'Keep agent traces',23);
label(g,L.pad,L.serveH-L.pad,'Update task history',21,400,C.muted);
arrow(svg,[[col(0)+L.card-L.pad,L.serveY+9*L.pad+24],[col(1)-L.gap/2,L.serveY+9*L.pad+24],[col(1)-L.gap/2,L.serveY+L.serveH/2],[col(1)-3,L.serveY+L.serveH/2]],'arrival-to-service-check');
arrow(svg,[[col(1)+L.card,L.serveY+L.serveH/2],[col(2)-L.gap/4,L.serveY+L.serveH/2],[col(2)-L.gap/4,L.serveY+chooseY+20],[col(2)+L.pad-3,L.serveY+chooseY+20]],'service-check-yes','purple');
label(svg,col(1)+L.card+L.gap/2,L.serveY+chooseY+4,'Yes',18,400,C.purple,'middle');
arrow(svg,[[col(1)+L.card-L.pad,L.serveY+agentServeY+22],[col(2)+L.pad-4,L.serveY+agentServeY+22]],'no-budget-agent-service','red');
arrow(svg,[[col(2)+resultRail,L.serveY+resultY],[col(3)-3,L.serveY+resultY]],'service-to-record');
label(svg,col(2)+L.card/2,L.serveY+L.serveH+29,'Detected break: invalidate program',21,400,C.muted,'middle');

// Each budget check is connected directly to the cumulative cost budget.
rect(svg,L.margin,L.budgetY,W-2*L.margin,L.budgetH,'#fff',C.purple,'shared-budget');
icon(svg,'shield',L.margin+L.pad,L.budgetY+20,30,C.purple);
label(svg,L.margin+L.pad+L.icon+L.iconGap,L.budgetY+29,'Cumulative cost budget',25,600,C.purple);
label(svg,L.margin+L.pad+L.icon+L.iconGap,L.budgetY+55,'Routing + service + every compilation attempt',22);
label(svg,W-L.margin-L.pad,L.budgetY+30,'Kₜ ≤ (1 + ε) Aₜ',29,600,C.purple,'end');
label(svg,W-L.margin-L.pad,L.budgetY+55,'Aₜ: agent cost for observed tasks',21,400,C.muted,'end');
arrow(svg,[[col(1)+L.card/2,L.budgetY],[col(1)+L.card/2,L.serveY+L.serveH+4]],'budget-to-service','purple');
arrow(svg,[[col(2)+L.card/2,L.budgetY+L.budgetH],[col(2)+L.card/2,L.compileY-4]],'budget-to-compilation','purple');

// Online algorithm, stage 2: each condition must pass before an attempt is paid.
label(svg,L.margin+L.pad,L.compileY-58,'2  Decide whether to compile',25,600);
g=card(0,L.compileY,L.compileH,'eligibility','gray');title(g,'files','Eligible family','gray');
label(g,L.pad,5*L.pad,'k completed traces',23,600);
label(g,L.pad,5*L.pad+32,'No live program',23);

g=card(1,L.compileY,L.compileH,'cost-aware-proposal','green');title(g,'chart-column','Cost-aware proposal','green');
['Estimated uses ×','per-use saving','> compilation cost','+ routing'].forEach((s,i)=>label(g,L.pad,4*L.pad+8+i*28,s,22,600));
label(g,L.pad,L.compileH-L.pad,'Includes failed attempts',21,400,C.muted);

g=card(2,L.compileY,L.compileH,'compilation-budget','purple');title(g,'shield','Compilation budget','purple');
label(g,L.pad,5*L.pad,'Reserve cost of',23);
label(g,L.pad,5*L.pad+32,'success or failure',23);

g=card(3,L.compileY,L.compileH,'compile-and-verify','green');title(g,'code-xml','Compile / verify','green');
node(g,4*L.pad,48,'Store program','store-program','green','circle-check');
node(g,7*L.pad,44,'No program','no-program-result','red','circle-x');
label(g,L.pad,L.compileH-L.pad,'Record cost + outcome',22,400,C.amber);
for(let i=0;i<3;i++){link(i,L.compileY+L.compileH/2,`compile-yes-${i}`);label(svg,col(i)+L.card+L.gap/2,L.compileY+L.compileH/2-12,'Yes',18,400,C.ink,'middle');}
const waitY=L.compileY+L.compileH+58,waitW=col(2)+L.card-col(0);
rect(svg,col(0),waitY,waitW,48,C.grayBg,C.gray,'wait-for-next-task');
label(svg,col(0)+waitW/2,waitY+32,'Wait for the next task',23,400,C.ink,'middle');
const failedRail=col(3)-L.gap/2;
arrow(svg,[[col(3)+L.pad,L.compileY+7*L.pad+22],[failedRail,L.compileY+7*L.pad+22],
  [failedRail,waitY+24],[col(0)+waitW+3,waitY+24]],'failed-compilation-to-wait','red');
for(let i=0;i<3;i++){
  arrow(svg,[[col(i)+L.card/2,L.compileY+L.compileH],[col(i)+L.card/2,waitY-3]],`compile-no-${i}`,'gray');
  label(svg,col(i)+L.card/2+12,waitY-22,'No',20,400,C.muted);
}
rect(svg,col(3),waitY,L.card,68,C.greenBg,C.green,'saved-program-output');
const artifactCX=col(3)+L.pad+15,artifactCY=waitY+33,r=18,depth=4;
svg.append('path').attr('d',`M${artifactCX-r},${artifactCY-r} l${depth},${-depth} h${2*r} v${2*r} l${-depth},${depth} Z`)
  .attr('fill',S.programDepthFill).attr('stroke',C.green).attr('stroke-width',0.9).attr('data-node','stored-program-depth');
rect(svg,artifactCX-r,artifactCY-r,2*r,2*r,P.progFill,C.green,'stored-program-face',3);
icon(svg,S.icons.program,artifactCX-15,artifactCY-15,30,C.green);
label(svg,col(3)+L.pad+48,waitY+29,'Stored program',22,600,C.green);
label(svg,col(3)+L.pad+48,waitY+54,'For later tasks',20,400,C.green);
const savedRail=col(3)+L.card+8;
arrow(svg,[[col(3)+L.card-L.pad,L.compileY+4*L.pad+24],[savedRail,L.compileY+4*L.pad+24],
  [savedRail,waitY-20],[col(3)+L.card/2,waitY-20],[col(3)+L.card/2,waitY-3]],'store-to-saved-program','green');
const afterY=L.budgetY+L.budgetH+42, rail=W-L.margin/2-4;
const budgetLinkX=col(2)+L.card/2;
arrow(svg,[[col(3)+L.card,L.serveY+L.serveH/2],[rail,L.serveY+L.serveH/2],[rail,afterY],[budgetLinkX+6,afterY]],'after-service-before-crossing','ink',false);
arrow(svg,[[budgetLinkX-6,afterY],[col(0)+L.pad/2,afterY],[col(0)+L.pad/2,L.compileY-32],[col(0)+L.card/2,L.compileY-32],[col(0)+L.card/2,L.compileY-4]],'after-service-to-compilation');
label(svg,col(3)+L.card/2,afterY-14,'Task completed',21,400,C.ink,'middle');
label(svg,col(1)+L.card/2,L.compileY-26,'Costs + arrivals + outcomes',20,400,C.blue,'middle');
arrow(svg,[[col(1)+L.card/2,L.compileY-20],[col(1)+L.card/2,L.compileY-4]],'history-to-proposal','blue');
}
}

const crop=PART==='measurement'?{y:0,h:L.measureY+L.measureH+L.pad+44+18}:
  PART==='online'?{y:L.serveY-44,h:H-(L.serveY-44)}:{y:0,h:H};
svg.attr('height',crop.h).attr('viewBox',`0 ${crop.y} ${W} ${crop.h}`)
  .attr('data-figure',`pace-${PART}-review-v3`);
svg.select('[data-layer="background"]').attr('y',crop.y).attr('height',crop.h);
