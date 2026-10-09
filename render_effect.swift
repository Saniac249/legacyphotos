import Foundation
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments

guard args.count == 5 else {
    print("""
    Usage:
      swift render_effect.swift <input.jpg> <effectName> <orientation> <output.jpg>

    Example:
      swift render_effect.swift /path/to/input.jpg Process 6 /path/to/output.jpg
    """)
    exit(1)
}

let inputPath = args[1]
let effectName = args[2]
let orientation = Int32(args[3]) ?? 1
let outputPath = args[4]

let effectMap: [String: String] = [
    "Chrome": "CIPhotoEffectChrome",
    "Fade": "CIPhotoEffectFade",
    "Instant": "CIPhotoEffectInstant",
    "Mono": "CIPhotoEffectMono",
    "Noir": "CIPhotoEffectNoir",
    "Process": "CIPhotoEffectProcess",
    "Tonal": "CIPhotoEffectTonal",
    "Transfer": "CIPhotoEffectTransfer"
]

guard let ciFilterName = effectMap[effectName] else {
    print("Unsupported effectName: \(effectName)")
    exit(1)
}

let inputURL = URL(fileURLWithPath: inputPath)
let outputURL = URL(fileURLWithPath: outputPath)

guard let inputCIImage = CIImage(contentsOf: inputURL) else {
    print("Could not load input image: \(inputPath)")
    exit(1)
}

// Apply the effect first
guard let filter = CIFilter(name: ciFilterName) else {
    print("Could not create CIFilter: \(ciFilterName)")
    exit(1)
}

filter.setValue(inputCIImage, forKey: kCIInputImageKey)

guard var outputCIImage = filter.outputImage else {
    print("Filter produced no output.")
    exit(1)
}

// Apply EXIF orientation from adjustment metadata
outputCIImage = outputCIImage.oriented(forExifOrientation: Int32(orientation))

let context = CIContext(options: nil)

guard let cgImage = context.createCGImage(outputCIImage, from: outputCIImage.extent) else {
    print("Could not create CGImage output.")
    exit(1)
}

guard let destination = CGImageDestinationCreateWithURL(
    outputURL as CFURL,
    UTType.jpeg.identifier as CFString,
    1,
    nil
) else {
    print("Could not create image destination.")
    exit(1)
}

let jpegOptions: CFDictionary = [
    kCGImageDestinationLossyCompressionQuality: 0.98
] as CFDictionary

CGImageDestinationAddImage(destination, cgImage, jpegOptions)

if CGImageDestinationFinalize(destination) {
    print("Wrote output to: \(outputPath)")
} else {
    print("Failed to write output image.")
    exit(1)
}