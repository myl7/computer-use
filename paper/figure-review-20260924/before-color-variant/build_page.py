from pathlib import Path
import base64
import html
import json
import re

ROOT = Path(__file__).resolve().parent


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def caption(source, label=None):
    text = source.read_text()
    if label:
        match = re.search(r"\\caption\{(.*?)\}\s*\\label\{" + label + r"\}", text, re.S)
    else:
        match = re.search(r"\\caption\{(.*?)\}\s*$", text, re.S)
    return " ".join(match.group(1).split())


old_captions = {
    "teaser": caption(ROOT / "old/body.tex", "fig:teaser"),
    "workflow": caption(ROOT / "workflow-before-split/workflow-caption.tex"),
}
new_captions = {name: caption(ROOT / f"new/{name}-caption.tex") for name in old_captions}
metrics = json.loads((ROOT / "verification.json").read_text())
sections = []
for num, name, title, change in [
    (1, "teaser", "Teaser", "Same procedure 改为常规字重。下方两个挑战的后果统一放入对齐的浅红提示框；右侧直接说明复用次数不足以收回编译成本。"),
    (2, "workflow", "Measurement & online algorithm", "左侧保留拆分前版本，右侧为当前两张独立图。三张图统一使用 teaser 的深灰、绿色、赭黄色和红色，沿用相同字体、边框与 Lucide 图标。模型调用统一用机器人头，程序产物使用同款绿色代码卡片。"),
]:
    cards = []
    for version, label, caps in [("old", "原版", old_captions), ("new", "本轮修改", new_captions)]:
        if name == "workflow" and version == "new":
            parts = []
            for fig_num, part, part_title in [(2,"measurement","Measurement"),(3,"online-algorithm","Online Algorithm")]:
                picture = data_uri(ROOT / f"new/{part}.svg", "image/svg+xml")
                cap = caption(ROOT / f"new/{part}-caption.tex")
                parts.append(f'''<div class="split-figure" id="new-{part}">
  <h4>Figure {fig_num} · {part_title}</h4>
  <div class="figure-stage"><button class="zoom" aria-label="放大 Figure {fig_num} {part_title}" data-title="Figure {fig_num} · {part_title}"><img src="{picture}" alt="Figure {fig_num}: {part_title}"></button></div>
  <div class="caption" lang="en"><span class="caption-title">Figure {fig_num}.</span> {html.escape(cap)}</div>
  <div class="files"><a href="new/{part}.pdf" target="_blank">PDF ↗</a><a href="new/{part}.svg" target="_blank">SVG ↗</a><a href="new/{part}-caption.tex">Caption LaTeX ↗</a></div>
</div>''')
            cards.append('<article class="version new" data-version="new"><div class="version-heading"><h3>本轮修改 · 拆分为两张图</h3><span>对应章节开头展示</span></div>'+''.join(parts)+'</article>')
            continue
        ext = "png" if version == "old" else "svg"
        mime = "image/png" if version == "old" else "image/svg+xml"
        asset_version = "workflow-before-split" if name == "workflow" and version == "old" else version
        if asset_version == "workflow-before-split": label = "上一版（拆分前）"
        img = data_uri(ROOT / asset_version / f"{name}.{ext}", mime)
        words = 179 if asset_version == "workflow-before-split" else metrics[f"{version}-{name}"]["words"]
        cards.append(f'''<article class="version {version}" data-version="{version}">
  <div class="version-heading"><h3>{label}</h3><span>图内 {words} 词</span></div>
  <div class="figure-stage" data-name="{name}">
    <button class="zoom" aria-label="放大 Figure {num} {label}" data-title="Figure {num} · {label}"><img src="{img}" alt="Figure {num}: {title}, {label}"></button>
  </div>
  <div class="caption" lang="en"><span class="caption-title">Figure {num}.</span> {html.escape(caps[name])}</div>
  <div class="files"><a href="{asset_version}/{name}.pdf" target="_blank">PDF ↗</a>{f'<a href="new/{name}.svg" target="_blank">SVG ↗</a><a href="new/{name}-caption.tex">Caption LaTeX ↗</a>' if version == 'new' else ''}</div>
</article>''')
    sections.append(f'''<section id="figure-{num}" data-section="figure-{num}">
<div class="section-heading"><h2><span>FIGURE {num:02}</span>{title}</h2><p>{change}</p></div>
<div class="compare {name}">{''.join(cards)}</div>
</section>''')

page = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PACE · Figure 1–3 新旧对比</title>
<style>
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:85px}body{margin:0;background:#f5f6f8;color:#26323c;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{padding:36px 40px 26px;max-width:1800px;margin:auto}.eyebrow{font-size:12px;letter-spacing:.14em;color:#687881;font-weight:650}h1{font-size:30px;letter-spacing:-.6px;margin:12px 0}header p{font-size:15px;color:#626f78;margin:0;line-height:1.7}
nav{position:sticky;top:0;z-index:5;background:#ffffffef;backdrop-filter:blur(12px);border-block:1px solid #dfe4e8;padding:12px 40px;display:flex;align-items:center;gap:22px}nav a{color:#3d505d;text-decoration:none;font-size:14px}nav a:hover{color:#246f62}nav .spacer{flex:1}button{font:inherit}nav button{background:white;border:1px solid #cdd5dc;border-radius:6px;padding:7px 12px;color:#465761;font-size:13px;cursor:pointer}nav button.active{border-color:#2d7569;color:#2d7569;background:#ecf6f2}nav .hint{font-size:12px;color:#72808a}
main{max-width:1800px;margin:auto;padding:0 40px 40px}section{margin-top:38px}.section-heading{margin-bottom:19px}h2{font-size:23px;margin:0 0 10px;font-weight:620}.section-heading h2 span{font-size:12px;letter-spacing:.1em;display:block;color:#75848f;margin-bottom:9px}.section-heading p{font-size:14px;color:#5c6a74;line-height:1.8;margin:0;max-width:1000px}
.compare{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:22px;align-items:start}.version{background:white;border:1px solid #dbe1e6;border-radius:10px;overflow:hidden}.version.new{border-color:#abc9bf}.version-heading{padding:15px 20px;border-bottom:1px solid #e8ebee;display:flex;align-items:center;justify-content:space-between;background:#fafbfc}.new .version-heading{background:#f0f7f3}h3{font-size:15px;margin:0}.version-heading span{font-size:12px;color:#72818b}.figure-stage{padding:20px 14px 4px;display:flex;align-items:flex-start}.teaser .figure-stage{aspect-ratio:960/716}.workflow .figure-stage{aspect-ratio:auto}.split-figure h4{margin:22px 22px 0;color:#42695e;font-size:17px;font-weight:600}.split-figure+.split-figure{border-top:2px solid #d6e3dd;margin-top:12px;padding-top:6px}.zoom{display:block;width:100%;border:0;background:white;margin:0;padding:0;cursor:zoom-in}.zoom img{display:block;width:100%;height:auto}.caption{font-family:Georgia,"Times New Roman",serif;font-size:15px;line-height:1.6;margin:0 22px;padding:19px 0;border-top:1px solid #e9edf0;color:#323d44}.caption-title{font-weight:700}.files{display:flex;gap:18px;padding:0 22px 19px;font-size:12px}.files a{color:#486c67;text-decoration:none}.files a:hover{text-decoration:underline}
body.paper .compare{grid-template-columns:minmax(0,574px) minmax(0,574px);justify-content:center}body.paper .figure-stage{padding-inline:22px}body.paper .caption{font-size:14px}footer{font-size:12px;line-height:1.8;color:#71808a;padding:26px 0 0;border-top:1px solid #dfe4e8;margin-top:36px}footer a{color:#57766d}dialog{border:1px solid #c6d3d0;border-radius:10px;width:min(1320px,96vw);max-height:95vh;padding:0}dialog::backdrop{background:#132127ad;backdrop-filter:blur(4px)}.dialog-toolbar{position:sticky;top:0;display:flex;justify-content:space-between;align-items:center;padding:13px 20px;background:white;border-bottom:1px solid #e1e7e4;font-size:14px}.dialog-toolbar button{border:1px solid #cbd6d2;border-radius:5px;background:white;padding:6px 12px;cursor:pointer}dialog img{width:100%;display:block;padding:20px;background:white}
@media(max-width:900px){header{padding:25px 18px}h1{font-size:25px}nav{padding:10px 18px;gap:15px}.hint{display:none}main{padding:0 18px 24px}.compare{gap:12px}.caption{font-size:13px;margin-inline:12px}.version-heading{padding:12px}.figure-stage{padding:12px 6px 4px}.section-heading p{font-size:13px}.files{padding-inline:12px;gap:10px;font-size:11px}}
@media(max-width:620px){.compare,body.paper .compare{grid-template-columns:500px 500px;justify-content:start;overflow-x:auto;padding-bottom:12px}nav button{font-size:11px}nav{gap:13px}.section-heading h2{font-size:20px}}
</style></head><body>
<header><div class="eyebrow">PACE / ICLR 2027 / FIGURE REVIEW</div><h1>Figure 1–3 · 新旧对比</h1><p>左侧为本轮修改前的图和 caption，右侧为当前修改版本。点击任意图可放大。</p></header>
<nav><a href="#figure-1">Figure 1 · Teaser</a><a href="#figure-2">Figure 2–3 · Methods</a><span class="spacer"></span><span class="hint">两侧按相同宽度显示</span><button id="paper-toggle" aria-pressed="false">切换论文宽度</button></nav>
<main>''' + "".join(sections) + '''
<footer>Figure 1 的新图和 caption 已写入论文。Measurement 和 Online Algorithm 已拆分为 Figure 2、Figure 3，放在对应章节开头。<br>图标来自 <a href="https://github.com/Mizar77/ml-paper-icons" target="_blank">Mizar77/ml-paper-icons</a> 的 Lucide 集合，沿用本地版本并保留许可证。<br><a href="http://127.0.0.1:18924/app/computer-use/paper/figure-review-20260924/new/teaser.html" target="_blank">Teaser 论文版式预览 ↗</a> · <a href="http://127.0.0.1:18924/app/computer-use/paper/figure-review-20260924/new/online-algorithm.html" target="_blank">Online Algorithm 论文版式预览 ↗</a></footer>
</main><dialog id="zoom-dialog"><div class="dialog-toolbar"><strong id="dialog-title"></strong><button id="close-dialog">关闭 · Esc</button></div><img id="dialog-image" alt="放大的论文插图"></dialog>
<script>
const dialog=document.getElementById('zoom-dialog');
document.querySelectorAll('.zoom').forEach(button=>button.addEventListener('click',()=>{
 document.getElementById('dialog-title').textContent=button.dataset.title;
 document.getElementById('dialog-image').src=button.querySelector('img').src;
 dialog.showModal();dialog.scrollTop=0;
}));
document.getElementById('close-dialog').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
document.getElementById('paper-toggle').addEventListener('click',event=>{
 const on=document.body.classList.toggle('paper');
 event.currentTarget.classList.toggle('active',on);event.currentTarget.setAttribute('aria-pressed',String(on));
 event.currentTarget.textContent=on?'恢复自适应宽度':'切换论文宽度';
});
</script></body></html>'''
(ROOT / "index.html").write_text(page)
print("Built self-contained comparison page: " + str(ROOT / "index.html"))
