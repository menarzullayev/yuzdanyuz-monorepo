(function(){
// Batch Image Trace for every pose in brand/mascot/poses/*.png (same settings as trace.jsx).
// Output: vector/poses/<name>.svg and <name>-preview.png (no .ai: the SVG plus this script reproduce it).
var BASE = "D:/Linux/Web_Projects/yuzdanyuz-monorepo/brand/mascot";
var src = new Folder(BASE + "/poses");
var out = new Folder(BASE + "/vector/poses");
if (!out.exists) out.create();
// ExtendScript's getFiles takes a mask string or a function, not a RegExp.
var files = src.getFiles(function (f) { return f instanceof File && /^\d\d-.*\.png$/i.test(decodeURI(f.name)); });
if (!files.length) return "no pose PNGs found in " + src.fsName;
app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
var log = [];
for (var i = 0; i < files.length; i++) {
  var f = files[i], nm = decodeURI(f.name).replace(/\.png$/i, "");
  try {
    var doc = app.documents.add(DocumentColorSpace.RGB, 1254, 1254);
    var p = doc.placedItems.add();
    p.file = f; p.width = 1254; p.height = 1254; p.position = [0, 1254];
    var plugin = p.trace();
    var o = plugin.tracing.tracingOptions;
    o.tracingMode = TracingModeType.TRACINGMODECOLOR;
    o.tracingColorTypeValue = TracingColorType.TRACINGLIMITEDCOLOR;
    o.colors = 12; o.pathFidelity = 85; o.cornerFidelity = 70; o.noiseFidelity = 15;
    o.fills = true; o.strokes = false; o.ignoreWhite = false;
    o.tracingMethod = TracingMethodType.TRACINGMETHODABUTTING;
    app.redraw();
    plugin.tracing.expandTracing();
    var n = doc.pathItems.length + doc.compoundPathItems.length;
    var so = new ExportOptionsSVG(); so.embedRasterImages = false; so.coordinatePrecision = 2;
    doc.exportFile(new File(BASE + "/vector/poses/" + nm + ".svg"), ExportType.SVG, so);
    doc.close(SaveOptions.DONOTSAVECHANGES);
    log.push(nm + " OK paths=" + n);
  } catch (e) {
    log.push(nm + " ERR " + e);
    try { app.activeDocument.close(SaveOptions.DONOTSAVECHANGES); } catch (e2) {}
  }
}
return log.join("\n");
})();
