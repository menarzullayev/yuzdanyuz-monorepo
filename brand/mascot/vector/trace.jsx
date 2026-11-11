(function(){
// Batch Image Trace for the mashqgoh mascot (Illustrator 2025), same settings style as TulporGo.
// Output per item: vector/<nm>.ai, vector/<nm>.svg, vector/<nm>-preview.png
var BASE = "D:/Linux/Web_Projects/yuzdanyuz-monorepo/brand/mascot";
var ITEMS = [
  // [source (relative to BASE), name, width, height, colors, pathFidelity, cornerFidelity, noiseFidelity]
  ["directions/01-qor-qoploni.png", "01-qor-qoploni", 1254, 1254, 12, 85, 70, 15],
  ["model-sheet/01-model-sheet-v1.png", "01-model-sheet-v1", 1536, 1024, 12, 85, 70, 15]
];
var outDir = new Folder(BASE + "/vector");
if (!outDir.exists) outDir.create();
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var log = [];
for (var i = 0; i < ITEMS.length; i++) {
  var it = ITEMS[i], nm = it[1], W = it[2], H = it[3];
  try {
    var doc = app.documents.add(DocumentColorSpace.RGB, W, H);
    var p = doc.placedItems.add();
    p.file = new File(BASE + "/" + it[0]);
    p.width = W; p.height = H;
    p.position = [0, H];
    var plugin = p.trace();
    var o = plugin.tracing.tracingOptions;
    o.tracingMode = TracingModeType.TRACINGMODECOLOR;
    o.tracingColorTypeValue = TracingColorType.TRACINGLIMITEDCOLOR;
    o.colors = it[4];
    o.pathFidelity = it[5]; o.cornerFidelity = it[6]; o.noiseFidelity = it[7];
    o.fills = true; o.strokes = false; o.ignoreWhite = false;
    o.tracingMethod = TracingMethodType.TRACINGMETHODABUTTING;
    app.redraw();
    plugin.tracing.expandTracing();
    var nPaths = doc.pathItems.length + doc.compoundPathItems.length;
    doc.saveAs(new File(BASE + "/vector/" + nm + ".ai"));
    var so = new ExportOptionsSVG();
    so.embedRasterImages = false; so.coordinatePrecision = 2;
    doc.exportFile(new File(BASE + "/vector/" + nm + ".svg"), ExportType.SVG, so);
    var po = new ExportOptionsPNG24();
    po.horizontalScale = 100; po.verticalScale = 100; po.antiAliasing = true; po.transparency = false; po.artBoardClipping = true;
    doc.exportFile(new File(BASE + "/vector/" + nm + "-preview.png"), ExportType.PNG24, po);
    doc.close(SaveOptions.DONOTSAVECHANGES);
    log.push(nm + " OK paths=" + nPaths);
  } catch (e) {
    log.push(nm + " ERR " + e);
    try { app.activeDocument.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
  }
}
return log.join("\n");
})();
