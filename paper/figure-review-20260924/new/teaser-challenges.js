    /* ================= challenge panels ================= */
    const GRID = { x: 10, y: 298, w: 580, h: 320, gap: 20, pad: 16 };
    const panel = i => ({ x: GRID.x + i * (GRID.w + GRID.gap), y: GRID.y, w: GRID.w, h: GRID.h });
    function panelFrame(p, key, accent) {
      const g = svg.append("g").attr("transform", `translate(${p.x},${p.y})`).attr("data-panel", key);
      box(g, 0, 0, p.w, p.h, "#fff", C.line, key + "-frame", 8);
      box(g, 0, 0, p.w, 4, accent, "none", key + "-accent", 2);
      return g;
    }
    function panelCaption(p, value) {
      text(svg, p.x+p.w/2, p.y+p.h+30, value, 24, 600, C.ink, "middle");
    }
    const pA = panel(0), pB = panel(1);
    const aC = panelFrame(pA, "uncertain-compilation-cost", C.cost);
    const a = { attemptX: 175, attemptY: 24, attemptW: 230, attemptH: 38,
      cardX: 46, firstY: 100, step: 51, cardH: 40, cardW: 518, barX: 420 };
    box(aC, a.attemptX, a.attemptY, a.attemptW, a.attemptH, C.inkFill, C.ink, "compilation-attempt");
    text(aC, a.attemptX+a.attemptW/2, a.attemptY+26, "Compilation attempt", 22, 600, C.ink, "middle");
    text(aC, a.cardX+38, a.firstY-11, "Outcome", 20, 400, C.muted);
    text(aC, a.cardX+a.cardW-10, a.firstY-11, "Token cost", 20, 400, C.muted, "end");
    const outcomes = [
      { key:"verified", title:"Program", width:82, color:C.prog, fill:C.progFill, icon:"code-xml" },
      { key:"rejected-early", title:"Failed early", width:42, color:C.fail, fill:C.failFill, icon:"circle-x" },
      { key:"rejected-repair", title:"Failed after repair", width:136, color:C.fail, fill:C.failFill, icon:"circle-x" }
    ];
    outcomes.forEach((o,i) => {
      const y=a.firstY+i*a.step, trunkX=a.cardX-22, trunkY=a.attemptY+a.attemptH+10;
      aC.append("path").attr("d",`M${a.attemptX+a.attemptW/2},${a.attemptY+a.attemptH} V${trunkY} H${trunkX} V${y+a.cardH/2} H${a.cardX-3}`)
        .attr("fill","none").attr("stroke",C.ink).attr("stroke-width",1.2)
        .attr("marker-end","url(#arrow-ink)").attr("data-edge",`compilation-to-${o.key}`);
      box(aC,a.cardX,y,a.cardW,a.cardH,o.fill,C.line,`outcome-${o.key}`);
      icon(aC,o.icon,a.cardX+10,y+9,23,o.color,`outcome-${o.key}`);
      text(aC,a.cardX+42,y+27,o.title,21,600,o.color);
      aC.append("rect").attr("x",a.barX).attr("y",y+15).attr("width",o.width).attr("height",10)
        .attr("rx",2).attr("fill",o.key==='verified'?C.cost:C.fail).attr("data-node",`cost-bar-${o.key}`);
    });
    const consequenceY = a.firstY+2*a.step+a.cardH+16;
    box(aC,GRID.pad,consequenceY,GRID.w-2*GRID.pad,46,C.failFill,"#DDB5AE","failed-spend-consequence");
    icon(aC,"table-2",GRID.pad+12,consequenceY+11,25,C.fail,"paid-failure");
    text(aC,GRID.pad+48,consequenceY+29,"Costly failures leave no usable program",21,600,C.fail);

    // Future reuse can leave an explicit unrecovered compilation balance.
    const bC = panelFrame(pB,"unknown-future-use",C.ink);
    const b = { labelX:GRID.pad, barX:320, barW:190, topY:30, firstY:111, step:56, barH:16 };
    text(bC,b.labelX,b.topY,"After successful compilation",21,600,C.ink);
    text(bC,b.barX,b.topY+25,"Cost to recover",19,400,C.cost);
    bC.append("rect").attr("x",b.barX).attr("y",b.topY+33).attr("width",b.barW).attr("height",10)
      .attr("rx",2).attr("fill",C.cost).attr("data-node","upfront-compilation-cost");
    const futures = [
      { key:"many", title:"Many tasks", recovered:1 },
      { key:"stop", title:"Tasks stop", recovered:0.2 },
      { key:"drift", title:"GUI drift", recovered:0.4 }
    ];
    futures.forEach((f,i) => {
      const cy=b.firstY+i*b.step;
      text(bC,b.labelX,cy+7,f.title,20,600,C.ink);
      const markX=b.labelX+140, markGap=40;
      if(f.key==='many') {
        for(let k=0;k<3;k++) taskMark(bC,markX+k*markGap,cy,`future-many-${k}`);
      } else if(f.key==='stop') {
        taskMark(bC,markX,cy,'future-stop-task');
      } else {
        taskMark(bC,markX,cy,'future-drift-task-1');
        taskMark(bC,markX+markGap,cy,'future-drift-task-2');
        icon(bC,'circle-x',markX+2*markGap-12,cy-12,24,C.fail,'broken-program');
      }
      const recovered=b.barW*f.recovered;
      bC.append('rect').attr('x',b.barX).attr('y',cy-b.barH/2).attr('width',b.barW).attr('height',b.barH)
        .attr('fill',C.failFill).attr('stroke',C.fail).attr('stroke-width',0.9).attr('data-node',`unrecovered-${f.key}`);
      bC.append('rect').attr('x',b.barX).attr('y',cy-b.barH/2).attr('width',recovered).attr('height',b.barH)
        .attr('fill',C.prog).attr('data-node',`recovered-${f.key}`);
      if(f.recovered<1) {
        bC.append('rect').attr('x',b.barX+recovered).attr('y',cy-b.barH/2).attr('width',b.barW-recovered).attr('height',b.barH)
          .attr('fill',C.fail).attr('opacity',0.82).attr('data-node',`net-loss-${f.key}`);
      }
      icon(bC,f.recovered===1?'circle-check':'circle-x',b.barX+b.barW+16,cy-11,22,f.recovered===1?C.prog:C.fail,`payback-${f.key}`);
    });
    const legendY=b.topY+30;
    [[C.prog,'Recovered'],[C.fail,'Unrecovered']].forEach(([color,value],i)=>{
      const y=legendY+i*24;
      bC.append('rect').attr('x',b.labelX).attr('y',y-11).attr('width',13).attr('height',10)
        .attr('fill',color).attr('data-legend-item',value);
      text(bC,b.labelX+21,y,value,19,400,C.muted);
    });
    box(bC,GRID.pad,consequenceY,GRID.w-2*GRID.pad,46,C.failFill,"#DDB5AE","unrecovered-cost-consequence");
    icon(bC,"table-2",GRID.pad+12,consequenceY+11,25,C.fail,"unrecovered-cost");
    text(bC,GRID.pad+48,consequenceY+29,"Too few reuses to pay back the cost",21,600,C.fail);
    panelCaption(pA,"(a) Uncertain compilation cost");
    panelCaption(pB,"(b) Unknown future use");
