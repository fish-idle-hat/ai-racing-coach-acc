import AppKit
import Foundation
import Vision

struct OCRItem: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

let args = CommandLine.arguments.dropFirst()
guard let imagePath = args.first else {
    fputs("{\"available\":false,\"error\":\"missing image path\",\"items\":[]}\n", stderr)
    exit(2)
}

guard let image = NSImage(contentsOfFile: imagePath),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("{\"available\":false,\"error\":\"could not load image\",\"items\":[]}")
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.minimumTextHeight = 0.015

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
    let items = (request.results ?? []).compactMap { observation -> OCRItem? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return OCRItem(
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.width,
            height: box.height
        )
    }
    let payload: [String: Any] = [
        "available": true,
        "items": items.map {
            [
                "text": $0.text,
                "confidence": $0.confidence,
                "x": $0.x,
                "y": $0.y,
                "width": $0.width,
                "height": $0.height,
            ]
        },
    ]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [])
    print(String(data: data, encoding: .utf8) ?? "{\"available\":false,\"error\":\"json encode failed\",\"items\":[]}")
} catch {
    let message = String(describing: error).replacingOccurrences(of: "\"", with: "'")
    print("{\"available\":false,\"error\":\"\(message)\",\"items\":[]}")
}
