// Four aligned columns and three flow rows share one spacing grid.
const L = { margin:24, gap:32, card:244, pad:18, icon:28, iconGap:18,
  measureY:54, measureH:400, serveY:620, serveH:310,
  budgetY:994, budgetH:68, compileY:1218, compileH:214 };
const col=i=>L.margin+i*(L.card+L.gap);
const W=2*L.margin+4*L.card+3*L.gap, H=L.compileY+L.compileH+148;
const C={ink:'#303740',muted:'#606C77',line:'#CDD5DC',
  blue:'#376F9B',blueBg:'#EFF5F9',green:'#2D7569',greenBg:'#EDF6F1',
  amber:'#93661F',amberBg:'#FBF4E6',red:'#AB514B',redBg:'#FAEFED',
  purple:'#705AA0',purpleBg:'#F3EFF8',gray:'#697681',grayBg:'#F6F7F9'};
const svg=d3.select('#chart').append('svg').attr('xmlns','http://www.w3.org/2000/svg')
  .attr('width',W).attr('height',H).attr('viewBox',`0 0 ${W} ${H}`)
  .attr('data-figure','pace-workflow-review-v2').style('font-family','"Source Sans 3", Arial, sans-serif');
svg.append('rect').attr('width',W).attr('height',H).attr('fill','#fff').attr('data-layer','background');
const defs=svg.append('defs');
for(const [name,paths] of Object.entries(ICONS)) defs.append('g').attr('id',`i-${name}`)
  .attr('fill','none').attr('stroke','currentColor').attr('stroke-width',1.65)
  .attr('stroke-linecap','round').attr('stroke-linejoin','round').html(paths);
for(const k of ['ink','blue','green','red','purple','amber','gray']) defs.append('marker')
  .attr('id',`a-${k}`).attr('viewBox','0 0 10 8').attr('refX',9).attr('refY',4)
  .attr('markerWidth',6).attr('markerHeight',5).attr('orient','auto')
  .append('path').attr('d','M0,0 L10,4 L0,8 Z').attr('fill',C[k]);
function rect(g,x,y,w,h,fill,stroke,key,r=7){return g.append('rect').attr('x',x).attr('y',y)
  .attr('width',w).attr('height',h).attr('rx',r).attr('fill',fill).attr('stroke',stroke)
  .attr('stroke-width',1.25).attr('data-node',key);}
function label(g,x,y,s,size=23,weight=400,color=C.ink,anchor='start'){return g.append('text')
  .attr('x',x).attr('y',y).attr('font-size',size).attr('font-weight',weight).attr('fill',color)
  .attr('text-anchor',anchor).attr('data-label',s).text(s);}
function icon(g,name,x,y,size=L.icon,color=C.ink){const n=g.append('g')
  .attr('transform',`translate(${x},${y}) scale(${size/24})`).attr('color',color).attr('data-icon',name);
  n.append('use').attr('href',`#i-${name}`);return n;}
function arrow(g,points,key,color='ink',dash=false){return g.append('path').attr('d',d3.line()(points))
  .attr('fill','none').attr('stroke',C[color]).attr('stroke-width',1.6)
  .attr('stroke-dasharray',dash?'5 4':null).attr('marker-end',`url(#a-${color})`).attr('data-edge',key);}
function card(i,y,h,key,color='blue',w=L.card){const g=svg.append('g').attr('transform',`translate(${col(i)},${y})`).attr('data-card',key);
  rect(g,0,0,w,h,C[color+'Bg'],C[color],key+'-frame');return g;}
function title(g,name,s,color='blue'){icon(g,name,L.pad,L.pad,L.icon,C[color]);
  label(g,L.pad+L.icon+L.iconGap,L.pad+24,s,23,600,C[color]);}
function node(g,y,h,s,key,color='blue',name=null,w=L.card-2*L.pad){
  rect(g,L.pad,y,w,h,'#fff',C[color],key);
  if(name){icon(g,name,2*L.pad,y+(h-L.icon)/2,L.icon,C[color]);
    label(g,2*L.pad+L.icon+L.iconGap,y+h/2+8,s,22,500,C[color]);}
  else label(g,L.pad+w/2,y+h/2+8,s,23,500,C[color],'middle');}
function region(y,h,key){rect(svg,L.margin/2,y,W-L.margin,h,'#fff',C.line,key,10);}
function link(i,y,key,color='ink'){arrow(svg,[[col(i)+L.card+2,y],[col(i+1)-4,y]],key,color);}
function artifact(g,x,y,w,h,key){const d=5;g.append('path').attr('d',`M${x},${y} l${d},${-d} h${w} v${h} l${-d},${d} Z`)
  .attr('fill','#D4E2ED').attr('stroke',C.blue).attr('data-node',key+'-depth');
  rect(g,x,y,w,h,'#fff',C.blue,key,3);icon(g,'clipboard-list',x+(w-28)/2,y+(h-28)/2,28,C.blue);}


if(PART !== 'online') {
// Measurement: input, compilation, paired service, and measured outputs.
{
let g=card(0,L.measureY,L.measureH,'source-traces');title(g,'files','Source traces');
const traceY=80, traceX=2*L.pad, extractY=180, verifyY=280, buildY=traceY;
const narrow=L.card-3*L.pad;
rect(g,L.pad,traceY-24,L.card-2*L.pad,96,'#fff',C.blue,'three-source-traces');
for(let i=2;i>=0;i--) artifact(g,traceX+i*6,traceY-i*5,43,49,`trace-${i}`);
label(g,traceX+76,traceY+15,'3 agent',24,600);label(g,traceX+76,traceY+44,'traces',24,600);
arrow(g,[[L.card/2,traceY+72],[L.card/2,extractY-4]],'traces-to-binding-model','blue');
node(g,extractY,48,'Model call','binding-extraction','blue','brain');
arrow(g,[[L.card/2,extractY+48],[L.card/2,verifyY-4]],'model-to-bindings','blue');
node(g,verifyY,48,'Bindings','verification-bindings','blue','table-2');

g=card(1,L.measureY,L.measureH,'compile-and-verify');title(g,'code-xml','Compile');
node(g,buildY,48,'Generate program','generate-program','blue',null,narrow);
arrow(g,[[L.pad+narrow/2,buildY+48],[L.pad+narrow/2,verifyY-4]],'program-to-verification','blue');
label(g,L.pad+narrow/2-12,(buildY+48+verifyY)/2+8,'Program',21,400,C.blue,'end');
node(g,verifyY,48,'Verify','verification','blue',null,narrow);
const repairX=L.card-L.pad/2;
arrow(g,[[L.pad+narrow,verifyY+24],[repairX,verifyY+24],[repairX,buildY+24],[L.pad+narrow+3,buildY+24]],'bounded-repair','red');
label(g,repairX-10,(verifyY+buildY)/2+22,'Bounded repair',19,400,C.red,'middle')
  .attr('transform',`rotate(-90,${repairX-10},${(verifyY+buildY)/2+22})`);
arrow(svg,[[col(0)+L.card-L.pad,L.measureY+traceY+24],[col(1)+L.pad-3,L.measureY+buildY+24]],'traces-to-code','blue');
arrow(svg,[[col(0)+L.card-L.pad,L.measureY+verifyY+24],[col(1)+L.pad-3,L.measureY+verifyY+24]],'bindings-to-verify','blue');
// This scope mark identifies what is repeated without implying new source runs.
const scopeLeft=col(0)+L.pad,scopeRight=col(1)+L.card-L.pad,scopeY=L.measureY-14;
svg.append('path').attr('d',`M${scopeLeft},${scopeY+7}V${scopeY}H${scopeRight}V${scopeY+7}`)
  .attr('fill','none').attr('stroke',C.blue).attr('data-edge','repeat-compilation-scope');
label(svg,(scopeLeft+scopeRight)/2,scopeY-12,'Repeat compilation from these traces',22,500,C.blue,'middle');

// Task prompts feed both serving paths. Only the program path extracts inputs.
const compareW=L.card+64, promptY=74, extractTaskY=140, runY=206, agentY=280;
g=card(2,L.measureY,L.measureH,'matched-serving','blue',compareW);title(g,'clipboard-list','Compare serving');
const innerW=compareW-2*L.pad;
node(g,promptY,40,'Task prompt','fresh-task-prompt','blue',null,innerW);
node(g,extractTaskY,44,'Extract inputs','deployment-extraction','green','brain',innerW);
node(g,runY,44,'Run program','program-service','green','code-xml',innerW);
node(g,agentY,44,'Run agent','matched-agent-run','blue','bot',innerW);
const mid=compareW/2, promptRail=compareW-L.pad/2;
arrow(g,[[mid,promptY+40],[mid,extractTaskY-3]],'prompt-to-extraction','blue');
arrow(g,[[mid,extractTaskY+44],[mid,runY-3]],'extracted-inputs-to-program','green');
arrow(g,[[compareW-L.pad,promptY+20],[promptRail,promptY+20],[promptRail,agentY+22],
  [compareW-L.pad+3,agentY+22]],'same-prompt-to-agent','blue');
const outcomeY=L.measureH-35;
rect(g,L.pad,outcomeY-16,innerW,36,'#fff',C.blue,'serving-outcome');
label(g,compareW/2,outcomeY+8,'Cost + task success',22,500,C.ink,'middle');
const outputRail=L.pad/2;
arrow(g,[[mid,runY+44],[mid,runY+58],[outputRail,runY+58],[outputRail,outcomeY],[L.pad-3,outcomeY]],'program-to-serving-outcome','green');
arrow(g,[[L.pad,agentY+22],[outputRail,agentY+22],[outputRail,outcomeY]],'agent-to-serving-outcome','blue');
const passRail=col(2)-L.gap/2;
arrow(svg,[[col(1)+L.pad+narrow,L.measureY+verifyY+24],[passRail,L.measureY+verifyY+24],
  [passRail,L.measureY+runY+22],[col(2)+L.pad-3,L.measureY+runY+22]],'verified-program-to-serving','green');
const passY=L.measureY+(verifyY+runY)/2+24;
label(svg,passRail-7,passY,'Pass',19,500,C.green,'middle').attr('transform',`rotate(-90,${passRail-7},${passY})`);

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
  lines.forEach((t,j)=>label(g,resultW/2,y+j*28,t,23,500,C.ink,'middle'));
});
arrow(svg,[[col(2)+compareW-L.pad,L.measureY+outcomeY],[resultX-3,L.measureY+outcomeY]],'serving-to-results','blue');
const ledgerY=L.measureY+L.measureH+L.pad,ledgerH=44;
rect(svg,col(1),ledgerY,L.card,ledgerH,C.amberBg,C.amber,'attempt-cost-record');
icon(svg,'table-2',col(1)+L.pad,ledgerY+8,28,C.amber);
label(svg,col(1)+L.pad+L.icon+L.iconGap,ledgerY+31,'Pass + fail costs',22,500,C.amber);
const costX=col(1)+L.pad+narrow/2;
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
for(const [i,s,dash] of [[2,'Flow',false],[3,'Shared budget',true]]){
  const x=col(i)+L.pad,y=L.serveY-21;
  arrow(svg,[[x,y],[x+32,y]],`legend-${i}`,dash?'purple':'ink',dash);
  label(svg,x+46,y+7,s,20,400,C.muted);
}
g=card(0,L.serveY,L.serveH,'task-arrives','gray');title(g,'calendar','Task arrives','gray');
label(g,L.pad,5*L.pad,'Update reference',23);
label(g,L.pad,7*L.pad,'Aₜ = Aₜ₋₁ + aₜ',28,600);
node(g,9*L.pad,48,'Task prompt','arriving-task-prompt','gray');
label(g,L.pad,L.serveH-L.pad,'Agent cost for this task',21,400,C.muted);

g=card(1,L.serveY,L.serveH,'service-budget','purple');title(g,'shield','Budget check','purple');
label(g,L.pad,5*L.pad,'Reserve routing,',23);
label(g,L.pad,5*L.pad+30,'service + fallback',23);
node(g,L.serveH-66,44,'Clear manifest','clear-manifest','red');
arrow(g,[[L.card/2,5*L.pad+36],[L.card/2,L.serveH-69]],'service-budget-no','red');
label(g,L.card/2+14,L.serveH-76,'No',19,500,C.red);

g=card(2,L.serveY,L.serveH,'serve-task','green');title(g,'bot','Serve task','green');
const chooseY=64, programY=118, agentServeY=244, choiceW=124;
node(g,chooseY,40,'Program?','program-available','green',null,choiceW);
rect(g,L.pad,programY,L.card-2*L.pad,72,'#fff',C.green,'online-program-service');
icon(g,'brain',2*L.pad,programY+8,24,C.green);
label(g,2*L.pad+24+L.iconGap,programY+28,'Prompt → inputs',20,500,C.green);
icon(g,'code-xml',2*L.pad,programY+40,24,C.green);
label(g,2*L.pad+24+L.iconGap,programY+60,'Run program',21,500,C.green);
node(g,agentServeY,44,'Agent','online-agent-service','blue','bot');
const choiceRail=L.card-3*L.pad, noRail=L.pad/2;
arrow(g,[[L.pad+choiceW,chooseY+20],[choiceRail,chooseY+20],[choiceRail,programY-4]],'program-available-yes','green');
label(g,choiceRail+10,chooseY+42,'Yes',18,500,C.green);
arrow(g,[[L.pad+choiceW/2,chooseY+40],[noRail,chooseY+40],[noRail,agentServeY+22],[L.pad-3,agentServeY+22]],'no-program-use-agent','blue');
label(g,L.pad+2,agentServeY-10,'No',18,500,C.blue);
arrow(g,[[L.card/2,programY+76],[L.card/2,agentServeY-3]],'detected-failure-to-agent','red');
label(g,L.card/2+12,agentServeY-10,'Detected fail',17,400,C.red);
const resultRail=L.card-L.pad/2, resultY=programY+72+L.pad;
arrow(g,[[L.card-L.pad,programY+24],[resultRail,programY+24],[resultRail,resultY],[L.card,resultY]],'program-service-complete','ink');
arrow(g,[[L.card-L.pad,agentServeY+22],[resultRail,agentServeY+22],[resultRail,resultY]],'agent-service-complete','ink');

g=card(3,L.serveY,L.serveH,'record-service','amber');title(g,'table-2','Record','amber');
label(g,L.pad,5*L.pad,'Charge actual cost',23,600);
label(g,L.pad,7*L.pad,'Keep agent traces',23);
label(g,L.pad,L.serveH-L.pad,'Update task history',21,400,C.muted);
arrow(svg,[[col(0)+L.card-L.pad,L.serveY+9*L.pad+24],[col(1)-3,L.serveY+9*L.pad+24]],'arrival-to-service-check');
arrow(svg,[[col(1)+L.card,L.serveY+chooseY+20],[col(2)+L.pad-3,L.serveY+chooseY+20]],'service-check-yes','purple');
label(svg,col(1)+L.card+L.gap/2,L.serveY+chooseY+4,'Yes',18,500,C.purple,'middle');
arrow(svg,[[col(1)+L.card-L.pad,L.serveY+agentServeY+22],[col(2)+L.pad-4,L.serveY+agentServeY+22]],'no-budget-agent-service','red');
link(2,L.serveY+resultY,'service-to-record');
label(svg,col(2)+L.card/2,L.serveY+L.serveH+29,'Detected break: invalidate program',21,400,C.muted,'middle');

// The two checks draw from one budget. Dashed links carry budget information.
rect(svg,L.margin,L.budgetY,W-2*L.margin,L.budgetH,C.purpleBg,C.purple,'shared-budget');
icon(svg,'shield',L.margin+L.pad,L.budgetY+20,30,C.purple);
label(svg,L.margin+L.pad+L.icon+L.iconGap,L.budgetY+29,'Shared cost budget',25,600,C.purple);
label(svg,L.margin+L.pad+L.icon+L.iconGap,L.budgetY+55,'Routing + service + every compilation attempt',22);
label(svg,W-L.margin-L.pad,L.budgetY+30,'Kₜ ≤ (1 + ε) Aₜ',29,600,C.purple,'end');
label(svg,W-L.margin-L.pad,L.budgetY+55,'Aₜ: agent cost for observed tasks',21,400,C.muted,'end');
arrow(svg,[[col(1)+L.card/2,L.budgetY],[col(1)+L.card/2,L.serveY+L.serveH+4]],'budget-to-service','purple',true);
arrow(svg,[[col(2)+L.card/2,L.budgetY+L.budgetH],[col(2)+L.card/2,L.compileY-4]],'budget-to-compilation','purple',true);

// Online algorithm, stage 2: each condition must pass before an attempt is paid.
label(svg,L.margin+L.pad,L.compileY-58,'2  Decide whether to compile',25,600);
g=card(0,L.compileY,L.compileH,'eligibility','gray');title(g,'files','Enough traces?','gray');
label(g,L.pad,5*L.pad,'3 agent traces',24,600);
label(g,L.pad,5*L.pad+32,'No live program',23);

g=card(1,L.compileY,L.compileH,'cost-aware-proposal','green');title(g,'chart-column','Worth the cost?','green');
['Expected uses ×','per-use saving','> compilation cost','+ routing'].forEach((s,i)=>label(g,L.pad,4*L.pad+8+i*28,s,22,600));
label(g,L.pad,L.compileH-L.pad,'Includes failed attempts',21,400,C.muted);

g=card(2,L.compileY,L.compileH,'compilation-budget','purple');title(g,'shield','Fits budget?','purple');
label(g,L.pad,5*L.pad,'Reserve cost of',23);
label(g,L.pad,5*L.pad+32,'success or failure',23);

g=card(3,L.compileY,L.compileH,'compile-and-verify','green');title(g,'code-xml','Compile / verify','green');
node(g,4*L.pad,48,'Store program','store-program','green','circle-check');
node(g,7*L.pad,44,'No program','no-program-result','red','circle-x');
label(g,L.pad,L.compileH-L.pad,'Record cost + outcome',22,500,C.amber);
for(let i=0;i<3;i++){link(i,L.compileY+L.compileH/2,`compile-yes-${i}`);label(svg,col(i)+L.card+L.gap/2,L.compileY+L.compileH/2-12,'Yes',18,500,C.ink,'middle');}
const waitY=L.compileY+L.compileH+58,waitW=col(2)+L.card-col(0);
rect(svg,col(0),waitY,waitW,48,C.grayBg,C.gray,'wait-for-next-task');
label(svg,col(0)+waitW/2,waitY+32,'Wait for the next task',23,500,C.ink,'middle');
const failedRail=col(3)-L.gap/2;
arrow(svg,[[col(3)+L.pad,L.compileY+7*L.pad+22],[failedRail,L.compileY+7*L.pad+22],
  [failedRail,waitY+24],[col(0)+waitW+3,waitY+24]],'failed-compilation-to-wait','red');
for(let i=0;i<3;i++){
  arrow(svg,[[col(i)+L.card/2,L.compileY+L.compileH],[col(i)+L.card/2,waitY-3]],`compile-no-${i}`,'gray');
  label(svg,col(i)+L.card/2+12,waitY-22,'No',20,500,C.muted);
}
rect(svg,col(3),waitY,L.card,68,C.greenBg,C.green,'saved-program-output');
icon(svg,'code-xml',col(3)+L.pad,waitY+18,30,C.green);
label(svg,col(3)+L.pad+48,waitY+29,'Saved program',22,600,C.green);
label(svg,col(3)+L.pad+48,waitY+54,'For later tasks',20,400,C.green);
const savedRail=col(3)+L.card+8;
arrow(svg,[[col(3)+L.card-L.pad,L.compileY+4*L.pad+24],[savedRail,L.compileY+4*L.pad+24],
  [savedRail,waitY-20],[col(3)+L.card/2,waitY-20],[col(3)+L.card/2,waitY-3]],'store-to-saved-program','green');
const afterY=L.budgetY+L.budgetH+42, rail=W-L.margin/2-4;
arrow(svg,[[col(3)+L.card,L.serveY+L.serveH/2],[rail,L.serveY+L.serveH/2],[rail,afterY],[col(0)+L.pad/2,afterY],[col(0)+L.pad/2,L.compileY-32],[col(0)+L.card/2,L.compileY-32],[col(0)+L.card/2,L.compileY-4]],'after-service-to-compilation');
label(svg,col(3)+L.card/2,afterY-14,'Task completed',21,500,C.ink,'middle');
label(svg,col(1)+L.card/2,L.compileY-26,'Costs + arrivals + outcomes',20,400,C.blue,'middle');
arrow(svg,[[col(1)+L.card/2,L.compileY-20],[col(1)+L.card/2,L.compileY-4]],'history-to-proposal','blue');
}
}

const crop=PART==='measurement'?{y:0,h:L.measureY+L.measureH+L.pad+44+18}:
  PART==='online'?{y:L.serveY-44,h:H-(L.serveY-44)}:{y:0,h:H};
svg.attr('height',crop.h).attr('viewBox',`0 ${crop.y} ${W} ${crop.h}`)
  .attr('data-figure',`pace-${PART}-review-v3`);
svg.select('[data-layer="background"]').attr('y',crop.y).attr('height',crop.h);
