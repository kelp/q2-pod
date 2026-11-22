// Prints the text macOS Vision reads in a PNG, one line per text run: "x y w h<TAB>text", in the image's
// pixels with y down. Build: swiftc -O ocr.swift -o ocr
import AppKit
import Vision

guard CommandLine.arguments.count == 2,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("usage: ocr IMAGE.png\n".data(using: .utf8)!)
    exit(2)
}
let w = Double(cg.width), h = Double(cg.height)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: cg).perform([request])
for case let o as VNRecognizedTextObservation in request.results ?? [] {
    guard let t = o.topCandidates(1).first else { continue }
    let b = o.boundingBox
    print(String(format: "%d %d %d %d\t%@", Int(b.minX * w), Int((1 - b.maxY) * h), Int(b.width * w),
                 Int(b.height * h), t.string))
}
