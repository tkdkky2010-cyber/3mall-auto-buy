// Continuity Camera 포함 모든 비디오 디바이스 탐색.
// 사용법:
//   ./continuity_probe                       → 디바이스 JSON 목록 출력
//   ./continuity_probe capture <uniqueID> <out.png>   → 한 프레임 PNG 저장

import AVFoundation
import AppKit
import CoreImage

let types: [AVCaptureDevice.DeviceType] = [
    .builtInWideAngleCamera,
    .external,
    .continuityCamera,
    .deskViewCamera,
]

let session = AVCaptureDevice.DiscoverySession(
    deviceTypes: types,
    mediaType: .video,
    position: .unspecified
)

let devices = session.devices
let args = CommandLine.arguments

func listDevices() {
    var arr: [[String: String]] = []
    for d in devices {
        arr.append([
            "name": d.localizedName,
            "uniqueID": d.uniqueID,
            "modelID": d.modelID,
            "type": d.deviceType.rawValue,
        ])
    }
    let data = try! JSONSerialization.data(withJSONObject: ["count": devices.count, "devices": arr], options: [.prettyPrinted])
    print(String(data: data, encoding: .utf8)!)
}

class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    var outPath: String
    var captured = false
    let sema = DispatchSemaphore(value: 0)
    init(outPath: String) { self.outPath = outPath }
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        if captured { return }
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ci = CIImage(cvPixelBuffer: pixelBuffer)
        let ctx = CIContext()
        guard let cg = ctx.createCGImage(ci, from: ci.extent) else { return }
        let rep = NSBitmapImageRep(cgImage: cg)
        if let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: outPath))
            captured = true
            sema.signal()
        }
    }
}

func capture(uniqueID: String, outPath: String) {
    guard let device = devices.first(where: { $0.uniqueID == uniqueID }) else {
        FileHandle.standardError.write("device not found: \(uniqueID)\n".data(using: .utf8)!)
        exit(2)
    }
    let session = AVCaptureSession()
    session.sessionPreset = .photo
    do {
        let input = try AVCaptureDeviceInput(device: device)
        if session.canAddInput(input) { session.addInput(input) }
    } catch {
        FileHandle.standardError.write("input error: \(error)\n".data(using: .utf8)!)
        exit(3)
    }
    let output = AVCaptureVideoDataOutput()
    let grabber = FrameGrabber(outPath: outPath)
    output.setSampleBufferDelegate(grabber, queue: DispatchQueue(label: "frame.q"))
    if session.canAddOutput(output) { session.addOutput(output) }
    session.startRunning()
    let result = grabber.sema.wait(timeout: .now() + 5.0)
    session.stopRunning()
    if result == .timedOut {
        FileHandle.standardError.write("capture timeout\n".data(using: .utf8)!)
        exit(4)
    }
    print("saved: \(outPath)")
}

if args.count >= 4 && args[1] == "capture" {
    capture(uniqueID: args[2], outPath: args[3])
} else {
    listDevices()
}
