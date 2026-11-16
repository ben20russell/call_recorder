import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

@available(macOS 14.2, *)
final class SystemAudioTap {
    struct Levels {
        let seconds: Double
        let rms: Double
        let peak: Double
    }

    private let originHostTime: UInt64
    private let stateLock = NSLock()
    private let ioQueue = DispatchQueue(label: "local.callrec.system-audio", qos: .userInitiated)
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?
    private var tapBuffers: UnsafeMutableAudioBufferListPointer?
    private var sourceFormat: AVAudioFormat?
    private var converter: AVAudioConverter?
    private var outputFormat: AVAudioFormat?
    private var outputFile: FileHandle?
    private var framesWritten: Int64 = 0
    private var currentRMS = 0.0
    private var currentPeak = 0.0
    private var failure: String?

    init(originHostTime: UInt64) {
        self.originHostTime = originHostTime
    }

    var levels: Levels {
        stateLock.lock()
        defer { stateLock.unlock() }
        return Levels(seconds: Double(framesWritten) / 16_000.0, rms: currentRMS, peak: currentPeak)
    }

    var failureMessage: String? {
        stateLock.lock()
        defer { stateLock.unlock() }
        return failure
    }

    func start(outputURL: URL) throws {
        guard FileManager.default.createFile(atPath: outputURL.path, contents: nil),
              let file = try? FileHandle(forWritingTo: outputURL) else {
            throw CaptureHelperError("Could not create \(outputURL.lastPathComponent).")
        }
        outputFile = file

        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.uuid = UUID()
        description.name = "Call Recorder"
        description.isPrivate = true
        description.muteBehavior = .unmuted

        var status = AudioHardwareCreateProcessTap(description, &tapID)
        guard status == noErr else {
            throw CaptureHelperError("Could not start system audio capture (Core Audio \(status)).",
                                     hint: "Allow System Audio Recording for Call Recorder in System Settings → Privacy & Security.")
        }

        do {
            var formatAddress = AudioObjectPropertyAddress(
                mSelector: kAudioTapPropertyFormat,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain)
            var streamDescription = AudioStreamBasicDescription()
            var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            status = AudioObjectGetPropertyData(tapID, &formatAddress, 0, nil, &formatSize, &streamDescription)
            guard status == noErr,
                  let inputFormat = AVAudioFormat(streamDescription: &streamDescription),
                  let pcmFormat = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                                sampleRate: 16_000,
                                                channels: 1,
                                                interleaved: true),
                  let converter = AVAudioConverter(from: inputFormat, to: pcmFormat) else {
                throw CaptureHelperError("Could not read the system audio format (Core Audio \(status)).")
            }
            sourceFormat = inputFormat
            outputFormat = pcmFormat
            self.converter = converter

            let outputUID = try Self.defaultOutputUID()
            let aggregate: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Call Recorder Audio",
                kAudioAggregateDeviceUIDKey: UUID().uuidString,
                kAudioAggregateDeviceMainSubDeviceKey: outputUID,
                kAudioAggregateDeviceIsPrivateKey: true,
                kAudioAggregateDeviceIsStackedKey: false,
                kAudioAggregateDeviceTapAutoStartKey: true,
                kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
                kAudioAggregateDeviceTapListKey: [[
                    kAudioSubTapDriftCompensationKey: true,
                    kAudioSubTapUIDKey: description.uuid.uuidString,
                ]],
            ]
            status = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID)
            guard status == noErr else {
                throw CaptureHelperError("Could not create the system audio stream (Core Audio \(status)).")
            }

            let neededBuffers = inputFormat.isInterleaved ? 1 : Int(inputFormat.channelCount)
            tapBuffers = AudioBufferList.allocate(maximumBuffers: neededBuffers)
            var proc: AudioDeviceIOProcID?
            status = AudioDeviceCreateIOProcIDWithBlock(&proc, aggregateID, ioQueue) { [weak self] _, input, inputTime, _, _ in
                guard let self else { return }
                self.consume(input, hostTime: inputTime.pointee.mHostTime,
                             hostTimeValid: inputTime.pointee.mFlags & kAudioTimeStampHostTimeValid != 0,
                             expectedBuffers: neededBuffers)
            }
            guard status == noErr, let proc else {
                throw CaptureHelperError("Could not attach the system audio reader (Core Audio \(status)).")
            }
            ioProcID = proc
            status = AudioDeviceStart(aggregateID, proc)
            guard status == noErr else {
                throw CaptureHelperError("Could not begin reading system audio (Core Audio \(status)).")
            }
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        if aggregateID != AudioObjectID(kAudioObjectUnknown) {
            if let ioProcID {
                AudioDeviceStop(aggregateID, ioProcID)
                AudioDeviceDestroyIOProcID(aggregateID, ioProcID)
            }
            AudioHardwareDestroyAggregateDevice(aggregateID)
        }
        if tapID != AudioObjectID(kAudioObjectUnknown) {
            AudioHardwareDestroyProcessTap(tapID)
        }
        if let tapBuffers {
            free(tapBuffers.unsafeMutablePointer)
        }
        try? outputFile?.close()
        tapBuffers = nil
        outputFile = nil
        ioProcID = nil
        aggregateID = AudioObjectID(kAudioObjectUnknown)
        tapID = AudioObjectID(kAudioObjectUnknown)
    }

    private func consume(_ input: UnsafePointer<AudioBufferList>,
                         hostTime: UInt64,
                         hostTimeValid: Bool,
                         expectedBuffers: Int) {
        guard let sourceFormat, let outputFormat, let converter, let outputFile else { return }
        let allBuffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
        var selectedBuffers: UnsafePointer<AudioBufferList> = input
        if allBuffers.count > expectedBuffers, let tapBuffers {
            for index in 0..<expectedBuffers {
                tapBuffers[index] = allBuffers[allBuffers.count - expectedBuffers + index]
            }
            selectedBuffers = UnsafePointer(tapBuffers.unsafePointer)
        }
        guard let inputBuffer = AVAudioPCMBuffer(pcmFormat: sourceFormat,
                                                 bufferListNoCopy: UnsafeMutablePointer(mutating: selectedBuffers),
                                                 deallocator: nil) else { return }
        let capacity = AVAudioFrameCount(ceil(Double(inputBuffer.frameLength) * 16_000 / sourceFormat.sampleRate) + 32)
        guard let converted = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var conversionError: NSError?
        converter.convert(to: converted, error: &conversionError) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return inputBuffer
        }
        guard conversionError == nil, converted.frameLength > 0,
              let sampleBytes = converted.audioBufferList.pointee.mBuffers.mData else { return }

        let sampleCount = Int(converted.frameLength)
        let samples = sampleBytes.assumingMemoryBound(to: Int16.self)
        let bytes = Data(bytes: sampleBytes, count: sampleCount * MemoryLayout<Int16>.size)
        let frameOffset = Self.frameOffset(hostTime: hostTime, valid: hostTimeValid, origin: originHostTime)
        stateLock.lock()
        if let frameOffset, frameOffset > framesWritten {
            let missing = frameOffset - framesWritten
            do {
                try Self.writeSilence(missing, to: outputFile)
                framesWritten = frameOffset
            } catch {
                failure = "Could not align system audio: \(error.localizedDescription)"
                stateLock.unlock()
                return
            }
        }
        do {
            try outputFile.write(contentsOf: bytes)
            framesWritten += Int64(sampleCount)
        } catch {
            failure = "Could not write system audio: \(error.localizedDescription)"
        }
        var sum = 0.0
        var peak = 0.0
        for index in 0..<sampleCount {
            let value = abs(Double(samples[index]) / 32768.0)
            sum += value * value
            peak = max(peak, value)
        }
        currentRMS = sampleCount > 0 ? sqrt(sum / Double(sampleCount)) : 0
        currentPeak = peak
        stateLock.unlock()
    }

    private static func frameOffset(hostTime: UInt64, valid: Bool, origin: UInt64) -> Int64? {
        guard valid, hostTime >= origin else { return nil }
        let nanos = AudioConvertHostTimeToNanos(hostTime - origin)
        return Int64(Double(nanos) * 16_000 / 1_000_000_000)
    }

    private static func writeSilence(_ frames: Int64, to file: FileHandle) throws {
        var remaining = frames
        let chunk = Data(repeating: 0, count: 32_000)
        while remaining > 0 {
            let amount = min(remaining, 16_000)
            try file.write(contentsOf: chunk.prefix(Int(amount) * 2))
            remaining -= amount
        }
    }

    private static func defaultOutputUID() throws -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        var status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                                &address, 0, nil, &size, &device)
        guard status == noErr, device != AudioObjectID(kAudioObjectUnknown) else {
            throw CaptureHelperError("Could not find the default audio output (Core Audio \(status)).")
        }
        address.mSelector = kAudioDevicePropertyDeviceUID
        var uid: CFString = "" as CFString
        size = UInt32(MemoryLayout<CFString>.size)
        status = AudioObjectGetPropertyData(device, &address, 0, nil, &size, &uid)
        guard status == noErr else {
            throw CaptureHelperError("Could not read the audio output identifier (Core Audio \(status)).")
        }
        return uid as String
    }
}

func systemAudioPermissionState() -> String {
    guard #available(macOS 14.2, *) else { return "unavailable" }
    let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
    description.uuid = UUID()
    description.isPrivate = true
    var tapID = AudioObjectID(kAudioObjectUnknown)
    let status = AudioHardwareCreateProcessTap(description, &tapID)
    guard status == noErr else {
        return status == kAudioHardwareNotPermittedError ? "denied" : "not_determined"
    }
    AudioHardwareDestroyProcessTap(tapID)
    return "granted"
}

func alignedFrameOffset(hostTime: UInt64, valid: Bool, origin: UInt64) -> Int64? {
    guard valid, hostTime >= origin else { return nil }
    let nanos = AudioConvertHostTimeToNanos(hostTime - origin)
    return Int64(Double(nanos) * 16_000 / 1_000_000_000)
}
