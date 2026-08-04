import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const OUT = "/Users/hm/Documents/UN-RDTII-ENGINE/pitch_day/RDTII_Live_Pitch_2min_2026.pptx";
const NOTES_OUT = "/Users/hm/Documents/UN-RDTII-ENGINE/pitch_day/RDTII_Live_Pitch_2min_2026_Speaker_Notes.txt";

const W = 1280;
const H = 720;
const C = {
  navy: "#0B2342",
  ink: "#102846",
  slate: "#56677A",
  pale: "#F5F8FB",
  white: "#FFFFFF",
  blue: "#1D78F2",
  cyan: "#28BDD0",
  coral: "#F46B61",
  yellow: "#F7B63B",
  mint: "#DDF5F1",
  line: "#D7E2EE",
};

function box(slide, geometry, left, top, width, height, fill = "none", lineFill = "none", radius = 0) {
  return slide.shapes.add({
    geometry,
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: lineFill, width: lineFill === "none" ? 0 : 1 },
    ...(radius ? { borderRadius: radius } : {}),
  });
}

function text(slide, value, left, top, width, height, size, color, opts = {}) {
  const s = box(slide, "textbox", left, top, width, height);
  s.text = value;
  s.text.style = {
    fontSize: size,
    color,
    bold: Boolean(opts.bold),
    italic: Boolean(opts.italic),
    ...(opts.lineSpacing ? { lineSpacing: opts.lineSpacing } : {}),
  };
  if (opts.verticalAlignment) s.text.verticalAlignment = opts.verticalAlignment;
  return s;
}

function line(slide, left, top, width, height, color = C.blue, widthPx = 2) {
  return slide.shapes.add({
    geometry: "line",
    position: { left, top, width, height },
    fill: "none",
    line: { style: "solid", fill: color, width: widthPx },
  });
}

function footer(slide, page, dark = false) {
  const col = dark ? "#BFD0E2" : C.slate;
  line(slide, 72, 684, 1136, 0, dark ? "#284463" : C.line, 1);
  text(slide, "Galaxefi  ·  UN Global Hackathon  ·  3 August 2026", 82, 691, 450, 18, 12, col);
  text(slide, String(page).padStart(2, "0"), 1166, 688, 44, 24, 12, dark ? C.cyan : C.blue, { bold: true });
}

function notes(slide, body) {
  slide.speakerNotes.textFrame.setText(`${body}\n\n[Sources]\n- Internal project evidence: pitch_day/deck/RDTII_Live_Pitch_2026.pptx\n- Internal architecture and run documentation: AGENTS.md\n[/Sources]`);
  slide.speakerNotes.setVisible(true);
}

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

function addCircle(slide, left, top, size, fill) {
  box(slide, "ellipse", left, top, size, size, fill, "none");
}

function addArrow(slide, left, top, width, color = C.blue) {
  const a = box(slide, "rightArrow", left, top, width, 28, color, "none");
  return a;
}

async function main() {
  const p = Presentation.create({ slideSize: { width: W, height: H } });

  // Slide 1 — open with the promise and proof.
  {
    const s = p.slides.add();
    s.background.fill = C.navy;
    text(s, "RDTII EXTRACTION ENGINE  ·  LIVE PITCH", 88, 80, 500, 24, 16, C.cyan, { bold: true });
    text(s, "Make regulatory\nevidence computable.", 86, 154, 700, 150, 54, C.white, { bold: true, lineSpacing: 0.9 });
    text(s, "From official source to reviewer-ready indicator output — with the evidence still visible.", 90, 338, 620, 64, 23, "#D9E8F5");
    box(s, "roundRect", 88, 472, 330, 78, "#163A63", "#2E6397", 14);
    text(s, "27 / 27", 112, 484, 120, 38, 30, C.white, { bold: true });
    text(s, "known indicator checks recovered", 112, 526, 260, 18, 15, "#D9E8F5");
    text(s, "$0.43", 454, 484, 130, 38, 30, C.cyan, { bold: true });
    text(s, "recorded Singapore P7 model cost", 454, 526, 270, 18, 15, "#D9E8F5");
    addCircle(s, 900, 0, 380, C.blue);
    addCircle(s, 1050, 350, 230, C.cyan);
    addCircle(s, 830, 500, 90, C.coral);
    footer(s, 1, true);
    notes(s, "Regulatory evidence is abundant, but it is scattered across portals, PDFs, scans, and languages. The RDTII Extraction Engine makes that evidence computable without hiding the source. In our completed six-run set, the evaluator recovered 27 out of 27 known indicator checks, and the Singapore Pillar 7 run logged a model cost of just $0.43.");
  }

  // Slide 2 — explain the flow.
  {
    const s = p.slides.add();
    s.background.fill = C.pale;
    text(s, "THE JOB", 82, 44, 250, 22, 16, C.blue, { bold: true });
    text(s, "The hard part is not finding laws.\nIt is proving what they say.", 80, 84, 780, 92, 40, C.ink, { bold: true, lineSpacing: 0.95 });
    text(s, "RDTII separates discovery from mapping so every output can be traced back to an official source.", 84, 195, 830, 36, 21, C.slate);

    const y = 308;
    const w = 235;
    box(s, "roundRect", 84, y, w, 172, C.navy, "none", 14);
    text(s, "01", 108, y + 24, 52, 26, 19, C.cyan, { bold: true });
    text(s, "Discover", 108, y + 58, 170, 30, 26, C.white, { bold: true });
    text(s, "Official portals\nRank · exclude\nKNOWN / NEW", 108, y + 98, 170, 48, 17, "#D9E8F5");
    addArrow(s, 336, y + 62, 78);
    box(s, "roundRect", 430, y, w, 172, C.white, C.coral, 14);
    text(s, "02", 454, y + 24, 52, 26, 19, C.coral, { bold: true });
    text(s, "Map", 454, y + 58, 170, 30, 26, C.ink, { bold: true });
    text(s, "Fetch · OCR\nTranslate · retrieve\nLLM mapping", 454, y + 98, 190, 48, 17, C.slate);
    addArrow(s, 682, y + 62, 78);
    box(s, "roundRect", 776, y, w, 172, C.white, C.cyan, 14);
    text(s, "03", 800, y + 24, 52, 26, 19, C.cyan, { bold: true });
    text(s, "Validate", 800, y + 58, 180, 30, 26, C.ink, { bold: true });
    text(s, "Exact snippet\nConfidence · notes\nCSV / JSON", 800, y + 98, 190, 48, 17, C.slate);

    box(s, "roundRect", 226, 540, 828, 56, C.mint, "#A8E4DC", 12);
    text(s, "source  →  provision  →  indicator  →  reviewer-ready output", 262, 556, 760, 24, 20, C.ink, { bold: true });
    footer(s, 2);
    notes(s, "The architecture has two zones, but the experience is one traceable chain. First, the engine finds and ranks relevant official documents and labels candidates as known or new. Then it fetches the document, runs extraction and retrieval, maps provisions to RDTII indicators, validates the evidence, and writes CSV or JSON. Throughout that process, the official source, exact provision, indicator, confidence, and reviewer notes stay visible.");
  }

  // Slide 3 — show a concrete result.
  {
    const s = p.slides.add();
    s.background.fill = C.pale;
    text(s, "SINGAPORE P7  ·  EVIDENCE DEMO", 82, 44, 420, 22, 16, C.blue, { bold: true });
    text(s, "One official Act, two traceable RDTII results", 80, 84, 1000, 48, 38, C.ink, { bold: true });
    text(s, "The engine preserves the source text while distinguishing new discoveries from known checks.", 84, 148, 900, 32, 21, C.slate);

    box(s, "roundRect", 80, 244, 492, 282, C.navy, "none", 14);
    text(s, "OFFICIAL SOURCE", 118, 278, 220, 22, 15, C.cyan, { bold: true });
    text(s, "Personal Data\nProtection Act 2012", 118, 320, 390, 72, 30, C.white, { bold: true, lineSpacing: 0.95 });
    text(s, "Singapore Statutes Online", 118, 412, 360, 32, 26, C.white, { bold: true });
    text(s, "sso.agc.gov.sg  ·  exact source text retained", 118, 488, 400, 20, 16, "#D9E8F5");

    addArrow(s, 602, 366, 64);
    box(s, "roundRect", 712, 264, 466, 100, C.white, C.line, 12);
    box(s, "roundRect", 738, 286, 96, 30, C.coral, "none", 8);
    text(s, "NEW", 755, 292, 62, 18, 13, C.white, { bold: true });
    text(s, "P7-I3  ·  Section 50(4)", 850, 283, 290, 24, 19, C.ink, { bold: true });
    text(s, "One-year investigation-record retention  ·  confidence 0.92", 850, 316, 290, 24, 15, C.slate);

    box(s, "roundRect", 712, 394, 466, 100, C.white, C.line, 12);
    box(s, "roundRect", 738, 416, 96, 30, C.cyan, "none", 8);
    text(s, "KNOWN", 750, 422, 76, 18, 13, C.white, { bold: true });
    text(s, "P7-I4  ·  Section 11(3)", 850, 413, 290, 24, 19, C.ink, { bold: true });
    text(s, "Designated data-protection function  ·  confidence 0.98", 850, 446, 290, 24, 15, C.slate);
    footer(s, 3);
    notes(s, "Here is the concrete output. From the official Singapore Statutes Online source, the run retained the source text and produced both a new provision candidate and a known indicator check. Those labels tell a reviewer whether the provision belongs to the reference set or needs policy review, while the confidence score and exact section remain attached to the result.");
  }

  // Slide 4 — future-extension hook.
  {
    const s = p.slides.add();
    s.background.fill = C.navy;
    text(s, "THE NEXT FRONTIER", 88, 78, 300, 22, 16, C.cyan, { bold: true });
    text(s, "What if a trader could ask:\n‘Can I ship this from A to B?’", 86, 132, 1000, 112, 44, C.white, { bold: true, lineSpacing: 0.92 });
    text(s, "The same evidence engine can grow into a trade-intelligence layer.", 90, 294, 900, 30, 21, "#D9E8F5");
    const cards = [
      [82, C.blue, "NOW", "Multiple pillars\nMultiple economies"],
      [423, C.cyan, "NEXT", "HS code\nOrigin + destination"],
      [764, C.blue, "THEN", "Official laws, regulations\nand trader steps"],
    ];
    for (const [x, fill, head, body] of cards) {
      box(s, "roundRect", x, 392, 300, 124, fill, "none", 12);
      text(s, head, x + 32, 418, 220, 22, 18, C.white, { bold: true });
      text(s, body, x + 32, 458, 250, 46, 17, C.white, { lineSpacing: 0.9 });
    }
    text(s, "One engine  →  broader coverage  →  actionable trade guidance", 90, 586, 760, 28, 21, C.cyan, { bold: true });
    text(s, "Built on official sources.", 934, 582, 280, 34, 21, C.cyan, { bold: true });
    footer(s, 4, true);
    notes(s, "This is where the opportunity opens up. We can extend the same configurable engine across multiple RDTII pillars and more economies. Then we can connect the evidence layer to an HS code, an origin country, and a destination country, so a trader could eventually find the relevant official laws, regulations, permits, and steps for a route. That future product would remain grounded in the source evidence we have just demonstrated.");
  }

  await fs.mkdir("/Users/hm/Documents/UN-RDTII-ENGINE/pitch_day/short_pitch_build/rendered", { recursive: true });
  for (const [index, slide] of p.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(`/Users/hm/Documents/UN-RDTII-ENGINE/pitch_day/short_pitch_build/rendered/${stem}.png`, await p.export({ slide, format: "png", scale: 1 }));
  }
  await writeBlob("/Users/hm/Documents/UN-RDTII-ENGINE/pitch_day/short_pitch_build/montage.webp", await p.export({ format: "webp", montage: true, scale: 1 }));
  const pptx = await PresentationFile.exportPptx(p);
  await pptx.save(OUT);

  const notesText = `RDTII Extraction Engine - 2-minute live pitch narration\n\nSlide 1\nRegulatory evidence is abundant, but it is scattered across portals, PDFs, scans, and languages. The RDTII Extraction Engine makes that evidence computable without hiding the source. In our completed six-run set, the evaluator recovered 27 out of 27 known indicator checks, and the Singapore Pillar 7 run logged a model cost of just $0.43.\n\nSlide 2\nThe architecture has two zones, but the experience is one traceable chain. First, the engine finds and ranks relevant official documents and labels candidates as known or new. Then it fetches the document, runs extraction and retrieval, maps provisions to RDTII indicators, validates the evidence, and writes CSV or JSON. Throughout that process, the official source, exact provision, indicator, confidence, and reviewer notes stay visible.\n\nSlide 3\nHere is the concrete output. From the official Singapore Statutes Online source, the run retained the source text and produced both a new provision candidate and a known indicator check. Those labels tell a reviewer whether the provision belongs to the reference set or needs policy review, while the confidence score and exact section remain attached to the result.\n\nSlide 4\nThis is where the opportunity opens up. We can extend the same configurable engine across multiple RDTII pillars and more economies. Then we can connect the evidence layer to an HS code, an origin country, and a destination country, so a trader could eventually find the relevant official laws, regulations, permits, and steps for a route. That future product would remain grounded in the source evidence we have just demonstrated.\n\nSources: internal project evidence in pitch_day/deck/RDTII_Live_Pitch_2026.pptx and internal architecture/run documentation in AGENTS.md; future extension direction supplied by the user.\n`;
  await fs.writeFile(NOTES_OUT, notesText, "utf8");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
