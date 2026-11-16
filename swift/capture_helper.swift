import AVFoundation
import CoreAudio
import AudioToolbox
import EventKit
import Foundation

private let outputLock = NSLock()
private let sampleRate = 16_000.0

struct CaptureHelperError: LocalizedError {
    let message: String
    let hint: String

    init(_ message: String, hint: String = "") {
        self.message = message
        self.hint = hint
    }

    var errorDescription: String? { message }
}

func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value),
          let line = String(data: data, encoding: .utf8) else { return }
    outputLock.lock()
    print(line)
    fflush(stdout)
    outputLock.unlock()
}

private func fail(_ message: String, hint: String = "") -> Never {
    var response: [String: Any] = ["event": "error", "message": message]
    if !hint.isEmpty { response["hint"] = hint }
    emit(response)
    exit(1)
}

private func permissionName(_ status: AVAuthorizationStatus) -> String {
    switch status {
    case .authorized: return "granted"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

private func calendarPermissionName() -> String {
    let status = EKEventStore.authorizationStatus(for: .event)
    if #available(macOS 14.0, *) {
        switch status {
        case .fullAccess: return "granted"
        case .writeOnly: return "write_only"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "not_determined"
        @unknown default: return "unknown"
        }
    }
    switch status {
    case .authorized: return "granted"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

private func permissions() {
    emit([
        "microphone": permissionName(AVCaptureDevice.authorizationStatus(for: .audio)),
        "system_audio": systemAudioPermissionState(),
        "calendar": calendarPermissionName(),
    ])
}

private func requestPermissions() {
    let microphone = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .audio) { _ in microphone.signal() }
    _ = microphone.wait(timeout: .now() + 180)

    let calendar = DispatchSemaphore(value: 0)
    let store = EKEventStore()
    if #available(macOS 14.0, *) {
        store.requestFullAccessToEvents { _, _ in calendar.signal() }
    } else {
        store.requestAccess(to: .event) { _, _ in calendar.signal() }
    }
    _ = calendar.wait(timeout: .now() + 180)
    _ = systemAudioPermissionState()
    permissions()
}

private func record(_ args: [String]) {
    guard let outIndex = args.firstIndex(of: "--out"), args.indices.contains(outIndex + 1) else {
        fail("record requires --out DIRECTORY")
    }
    let outDir = URL(fileURLWithPath: args[outIndex + 1], isDirectory: true)
    do {
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
    } catch {
        fail("Could not create the recording folder: \(error.localizedDescription)")
    }

    guard AVCaptureDevice.authorizationStatus(for: .audio) != .denied else {
        fail("Microphone access is off.",
             hint: "Allow microphone access in System Settings → Privacy & Security → Microphone.")
    }

    let engine = AVAudioEngine()
    let input = engine.inputNode
    if let deviceIndex = args.firstIndex(of: "--mic-device"), args.indices.contains(deviceIndex + 1) {
        guard let deviceID = AudioDeviceID(args[deviceIndex + 1]),
              let audioUnit = input.audioUnit else {
            fail("The selected microphone is not available.")
        }
        var selectedDevice = deviceID
        let status = AudioUnitSetProperty(audioUnit, kAudioOutputUnitProperty_CurrentDevice,
                                          kAudioUnitScope_Global, 0, &selectedDevice,
                                          UInt32(MemoryLayout<AudioDeviceID>.size))
        guard status == noErr else {
            fail("Could not select microphone \(deviceID) (Core Audio error \(status)).")
        }
    }
    let sourceFormat = input.outputFormat(forBus: 0)
    guard sourceFormat.sampleRate > 0, sourceFormat.channelCount > 0,
          let pcmFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: sampleRate,
                                        channels: 1, interleaved: true),
          let converter = AVAudioConverter(from: sourceFormat, to: pcmFormat) else {
        fail("The default microphone format is unavailable.")
    }

    let trackURL = outDir.appendingPathComponent("mic.pcm")
    guard FileManager.default.createFile(atPath: trackURL.path, contents: nil),
          let file = try? FileHandle(forWritingTo: trackURL) else {
        fail("Could not create mic.pcm in \(outDir.path).")
    }

    let stateLock = NSLock()
    var writtenFrames: Int64 = 0
    var rms: Double = 0
    var peak: Double = 0
    var micFailure: String?
    let originHostTime = AudioGetCurrentHostTime()
    input.installTap(onBus: 0, bufferSize: 2048, format: sourceFormat) { buffer, when in
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * sampleRate / sourceFormat.sampleRate) + 32)
        guard let converted = AVAudioPCMBuffer(pcmFormat: pcmFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var conversionError: NSError?
        converter.convert(to: converted, error: &conversionError) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return buffer
        }
        guard conversionError == nil, converted.frameLength > 0,
              let sampleBytes = converted.audioBufferList.pointee.mBuffers.mData else { return }
        let count = Int(converted.frameLength)
        let samples = sampleBytes.assumingMemoryBound(to: Int16.self)
        let byteCount = count * MemoryLayout<Int16>.size
        let data = Data(bytes: sampleBytes, count: byteCount)

        var squareSum = 0.0
        var maximum = 0.0
        for index in 0..<count {
            let value = abs(Double(samples[index]) / 32768.0)
            squareSum += value * value
            maximum = max(maximum, value)
        }
        stateLock.lock()
        if let frameOffset = alignedFrameOffset(hostTime: when.hostTime,
                                                valid: when.isHostTimeValid,
                                                origin: originHostTime),
           frameOffset > writtenFrames {
            do {
                try writeSilence(frameOffset - writtenFrames, to: file)
                writtenFrames = frameOffset
            } catch {
                micFailure = "Could not align microphone audio: \(error.localizedDescription)"
                stateLock.unlock()
                return
            }
        }
        do {
            try file.write(contentsOf: data)
            writtenFrames += Int64(count)
        } catch {
            micFailure = "Could not write microphone audio: \(error.localizedDescription)"
        }
        rms = count > 0 ? sqrt(squareSum / Double(count)) : 0
        peak = maximum
        stateLock.unlock()
    }

    do {
        try engine.start()
    } catch {
        input.removeTap(onBus: 0)
        try? file.close()
        fail("Could not start microphone capture: \(error.localizedDescription)",
             hint: "Check microphone access in System Settings → Privacy & Security → Microphone.")
    }

    var systemCapture: SystemAudioTap?
    var warnings: [String] = []
    if !args.contains("--no-system") {
        if #available(macOS 14.2, *) {
            let capture = SystemAudioTap(originHostTime: originHostTime)
            do {
                try capture.start(outputURL: outDir.appendingPathComponent("system.pcm"))
                systemCapture = capture
            } catch {
                capture.stop()
                try? FileManager.default.removeItem(at: outDir.appendingPathComponent("system.pcm"))
                warnings.append("System audio couldn't be captured: \(error.localizedDescription)")
            }
        } else {
            warnings.append("System audio capture requires macOS 14.2 or newer.")
        }
    }

    emit([
        "event": "started",
        "mic": true,
        "system": systemCapture != nil,
        "warnings": warnings,
    ])

    let stop = DispatchSemaphore(value: 0)
    DispatchQueue.global(qos: .utility).async {
        while let line = readLine() {
            if line.trimmingCharacters(in: .whitespacesAndNewlines) == "stop" {
                stop.signal()
                return
            }
        }
        stop.signal()
    }
    while stop.wait(timeout: .now() + 0.25) == .timedOut {
        stateLock.lock()
        let seconds = Double(writtenFrames) / sampleRate
        var event: [String: Any] = [
            "event": "levels",
            "mic": ["seconds": seconds, "rms": rms, "peak": peak],
        ]
        if let systemLevels = systemCapture?.levels {
            event["system"] = ["seconds": systemLevels.seconds,
                               "rms": systemLevels.rms,
                               "peak": systemLevels.peak]
        }
        let currentMicFailure = micFailure
        stateLock.unlock()
        if let currentMicFailure {
            emit(["event": "error", "message": currentMicFailure])
            break
        }
        if let message = systemCapture?.failureMessage {
            emit(["event": "source_restarted", "source": "system", "reason": message])
            systemCapture?.stop()
            systemCapture = nil
        }
        emit(event)
    }

    systemCapture?.stop()
    engine.stop()
    input.removeTap(onBus: 0)
    try? file.close()
    emit(["event": "stopped"])
}

private func writeSilence(_ frames: Int64, to file: FileHandle) throws {
    var remaining = frames
    let chunk = Data(repeating: 0, count: 32_000)
    while remaining > 0 {
        let amount = min(remaining, 16_000)
        try file.write(contentsOf: chunk.prefix(Int(amount) * 2))
        remaining -= amount
    }
}

private func listMicrophones() {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else {
        emit(["mics": []])
        return
    }
    let count = Int(size) / MemoryLayout<AudioDeviceID>.size
    var devices = [AudioDeviceID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &devices) == noErr else {
        emit(["mics": []])
        return
    }

    var defaultAddress = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var defaultDevice: AudioDeviceID = 0
    var defaultSize = UInt32(MemoryLayout<AudioDeviceID>.size)
    _ = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &defaultAddress, 0, nil,
                                   &defaultSize, &defaultDevice)

    let inputs = devices.compactMap { device -> [String: Any]? in
        var streamAddress = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamConfiguration,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain)
        var streamSize: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &streamAddress, 0, nil, &streamSize) == noErr else { return nil }
        let storage = UnsafeMutableRawPointer.allocate(byteCount: Int(streamSize), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { storage.deallocate() }
        guard AudioObjectGetPropertyData(device, &streamAddress, 0, nil, &streamSize, storage) == noErr else { return nil }
        let buffers = storage.assumingMemoryBound(to: AudioBufferList.self)
        let channelCount = UnsafeMutableAudioBufferListPointer(buffers).reduce(0) { $0 + Int($1.mNumberChannels) }
        guard channelCount > 0 else { return nil }

        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var name: CFString = "" as CFString
        var nameSize = UInt32(MemoryLayout<CFString>.size)
        guard AudioObjectGetPropertyData(device, &nameAddress, 0, nil, &nameSize, &name) == noErr else { return nil }
        return ["id": String(device), "name": name as String, "channels": channelCount,
                "default": device == defaultDevice]
    }
    emit(["mics": inputs])
}

private func events(_ args: [String]) {
    guard let fromIndex = args.firstIndex(of: "--from"), args.indices.contains(fromIndex + 1),
          let toIndex = args.firstIndex(of: "--to"), args.indices.contains(toIndex + 1),
          let start = ISO8601DateFormatter().date(from: args[fromIndex + 1]),
          let end = ISO8601DateFormatter().date(from: args[toIndex + 1]) else {
        emit(["event": "error", "message": "events requires valid --from and --to ISO-8601 dates."])
        return
    }
    let store = EKEventStore()
    let status = EKEventStore.authorizationStatus(for: .event)
    guard status == .authorized || (ifAvailableFullAccess(status)) else {
        emit(["event": "error", "message": "Calendar access is not allowed.",
              "hint": "Allow Call Recorder in System Settings → Privacy & Security → Calendars."])
        return
    }
    let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
    let formatter = ISO8601DateFormatter()
    let values = store.events(matching: predicate).map { event -> [String: Any] in
        let attendees = (event.attendees ?? []).map { person -> [String: Any] in
            [
                "name": person.name ?? "",
                "email": person.url?.absoluteString.replacingOccurrences(of: "mailto:", with: "") ?? "",
                "is_me": person.isCurrentUser,
                "status": person.participantStatus == .declined ? 3 : 0,
            ]
        }
        return [
            "id": event.eventIdentifier ?? UUID().uuidString,
            "title": event.title ?? "(No title)",
            "start": formatter.string(from: event.startDate),
            "end": formatter.string(from: event.endDate),
            "all_day": event.isAllDay,
            "location": event.location ?? "",
            "url": event.url?.absoluteString ?? "",
            "notes": event.notes ?? "",
            "calendar": event.calendar.title,
            "organizer": event.organizer?.url?.absoluteString ?? "",
            "attendees": attendees,
            "cancelled": event.status == .canceled,
        ]
    }
    emit(["events": values])
}

private func ifAvailableFullAccess(_ status: EKAuthorizationStatus) -> Bool {
    if #available(macOS 14.0, *) { return status == .fullAccess }
    return false
}

let command = CommandLine.arguments.dropFirst().first ?? ""
let args = Array(CommandLine.arguments.dropFirst())
switch command {
case "record":
    record(args)
case "permissions":
    permissions()
case "request-permissions":
    requestPermissions()
case "list-mics":
    listMicrophones()
case "events":
    events(args)
case "watch-mic":
    watchMicrophoneUsers()
case "popup":
    runPopup(Array(args.dropFirst()))
case "help", "--help", "-h":
    print("Usage: callrec-capture {record --out DIR|permissions|request-permissions|list-mics|events --from DATE --to DATE|watch-mic|popup}")
default:
    fail("Unknown capture helper command: \(command)")
}
