    /* ================= context strip ================= */
    const TOP = { x: 10, y: 10, h: 264, leftW: 520, gap: 28, rightW: 392, pad: 16 };
    const left = { x: TOP.x, y: TOP.y, w: TOP.leftW, h: TOP.h };
    const right = { x: left.x + left.w + TOP.gap, y: TOP.y, w: TOP.rightW, h: TOP.h };
    const strip = svg.append("g").attr("data-panel", "task-and-execution");
    box(strip, left.x, left.y, left.w, left.h, C.stripBg, "#D7DFE5", "example-task-panel", 10);
    box(strip, right.x, right.y, right.w, right.h, C.stripBg, "#D7DFE5", "execution-options-panel", 10);

    // The left card states the task; its rows show different task inputs.
    const cardX = left.x + TOP.pad, cardW = 330;
    const headingY = left.y + 36, taskTop = left.y + 58, taskStep = 40;
    icon(strip, "clipboard-list", cardX, headingY - 22, 25, C.ink, "example-task");
    text(strip, cardX + 33, headingY, "Example task: Add a calendar event", 21, 600, C.ink);
    const prompts = [
      ["Lab meeting", " · Oct 6, 10:00"],
      ["Project review", " · Oct 8, 14:00"],
      ["Reading group", " · Oct 13, 11:00"]
    ];
    prompts.forEach(([name, inputs], i) => {
      const y = taskTop + i * taskStep, h = 32;
      box(strip, cardX, y, cardW, h, "#fff", C.line, `prompt-card-${i + 1}`);
      strip.append("path").attr("d", `M${cardX},${y+h-13} l-7,9 h7`)
        .attr("fill", "#fff").attr("stroke", C.line).attr("stroke-width", 1.2)
        .attr("data-node", `prompt-tail-${i + 1}`);
      const t = strip.append("text").attr("x", cardX + 12).attr("y", y + 22)
        .attr("font-family", FONT).attr("font-size", 20).attr("data-node", `prompt-text-${i + 1}`);
      t.append("tspan").attr("fill", C.ink).text("Add ");
      t.append("tspan").attr("fill", C.ink).attr("font-weight", 600).text(name);
      t.append("tspan").attr("fill", C.muted).text(inputs);
    });
    // Center the explanatory group on the full stack of three task prompts.
    const taskStackCenter = taskTop + (2 * taskStep + 32) / 2;
    const groupX = cardX + cardW + 14, groupY = taskStackCenter - 33;
    text(strip, groupX, groupY, "Same procedure", 20, 400, C.ink);
    text(strip, groupX, groupY + 26, "Different inputs", 20, 400, C.muted);
    const calendarX = groupX + 28, calendarY = groupY + 46;
    icon(strip, "calendar", calendarX, calendarY, 34, C.ink, "add-calendar-event");
    strip.append("circle").attr("cx", calendarX + 33).attr("cy", calendarY + 28).attr("r", 10)
      .attr("fill", C.ink).attr("stroke", "#fff").attr("stroke-width", 2).attr("data-node", "add-event-badge");
    strip.append("path").attr("d", `M${calendarX+28},${calendarY+28} h10 M${calendarX+33},${calendarY+23} v10`)
      .attr("stroke", "#fff").attr("stroke-width", 1.8).attr("stroke-linecap", "round").attr("data-node", "add-event-plus");
    const procedureX = cardX + 26, procedureGap = 156, procedureY = left.y + left.h - 48;
    [["calendar", "Open"], ["text-cursor-input", "Fill"], ["circle-check", "Save"]].forEach(([symbol, label], i) => {
      const x = procedureX + i * procedureGap;
      icon(strip, symbol, x, procedureY, 28, C.ink, `procedure-${i + 1}`);
      text(strip, x + 37, procedureY + 23, label, 20, 400, C.ink);
      if (i < 2) strip.append("line").attr("x1", x + 103).attr("x2", x + procedureGap - 15)
        .attr("y1", procedureY + 14).attr("y2", procedureY + 14)
        .attr("stroke", C.ink).attr("stroke-width", 1.2).attr("marker-end", "url(#arrow-ink)")
        .attr("data-edge", `procedure-step-${i + 1}`);
    });

    // A single relation connects the example task to its execution options.
    strip.append("line").attr("x1", left.x + left.w + 3).attr("x2", right.x - 4)
      .attr("y1", TOP.y + TOP.h/2).attr("y2", TOP.y + TOP.h/2)
      .attr("stroke", C.ink).attr("stroke-width", 1.6).attr("marker-end", "url(#arrow-ink)")
      .attr("data-edge", "example-task-to-execution-options");

    // The right card spaces headings, artifacts, cost bars, and extraction apart.
    const RX = right.x + 18;
    text(strip, RX, right.y + 32, "Two ways to execute the task", 21, 600, C.ink);
    text(strip, RX, right.y + 60, "Agent: model calls for each task", 20, 600, C.ink);
    const taskColumns = [RX + 44, RX + 176, RX + 308];
    const agentY = right.y + 88, costY = agentY + 27;
    taskColumns.forEach((cx, i) => {
      taskMark(strip, cx, agentY, `agent-task-${i + 1}`);
      strip.append("rect").attr("x", cx - 35).attr("y", costY).attr("width", 70).attr("height", 8)
        .attr("rx", 2).attr("fill", C.cost).attr("opacity", 0.75).attr("data-node", `agent-cost-${i + 1}`);
    });
    text(strip, RX, right.y + 147, "Program: compile, then reuse", 20, 600, C.ink);
    const programY = right.y + 182, compileW = 94, compileH = 28;
    const compileX = taskColumns[0] - compileW/2, compileY = right.y + 207;
    taskMark(strip, taskColumns[0], programY, "agent-task-source");
    taskColumns.slice(1).forEach((cx, i) => taskMark(strip, cx, programY, `prog-task-${i + 1}`, C.prog));
    box(strip, compileX, compileY, compileW, compileH, C.costFill, C.cost, "compilation-cost-block");
    text(strip, taskColumns[0], compileY + 21, "Compile", 20, 600, C.cost, "middle");
    strip.append("line").attr("x1", taskColumns[0]).attr("x2", taskColumns[0])
      .attr("y1", programY + 15).attr("y2", compileY - 2)
      .attr("stroke", C.ink).attr("stroke-width", 1.2).attr("marker-end", "url(#arrow-ink)")
      .attr("data-edge", "agent-experience-to-compilation");
    // One compiled artifact supplies both reuse positions, without chaining executions.
    const artifactY = compileY + compileH/2;
    strip.append("line").attr("x1", compileX + compileW).attr("x2", taskColumns[2])
      .attr("y1", artifactY).attr("y2", artifactY)
      .attr("stroke", C.prog).attr("stroke-width", 1.2).attr("data-edge", "compiled-artifact-distribution");
    taskColumns.slice(1).forEach((cx,i) => {
      strip.append("line").attr("x1", cx).attr("x2", cx)
        .attr("y1", artifactY).attr("y2", programY + 20)
        .attr("stroke", C.prog).attr("stroke-width", 1.2).attr("marker-end", "url(#arrow-prog)")
        .attr("data-edge", `same-program-to-reuse-${i+1}`);
    });
    const extractionY = right.y + right.h - 17;
    icon(strip, "bot", RX + 110, extractionY - 18, 23, C.ink, "input-extraction-model");
    text(strip, RX + 142, extractionY, "Extract inputs per use", 20, 400, C.ink);
