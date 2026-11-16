import CoreAudio
import Foundation

func watchMicrophoneUsers() -> Never {
    while true {
        let apps: [[String: String]]
        if #available(macOS 14.4, *) {
            apps = microphoneUsers()
        } else {
            apps = []
        }
        emit(["apps": apps])
        Thread.sleep(forTimeInterval: 1)
    }
}

@available(macOS 14.4, *)
private func microphoneUsers() -> [[String: String]] {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyProcessObjectList,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    let system = AudioObjectID(kAudioObjectSystemObject)
    guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else { return [] }
    let count = Int(size) / MemoryLayout<AudioObjectID>.size
    guard count > 0 else { return [] }
    var processObjects = [AudioObjectID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &processObjects) == noErr else {
        return []
    }

    var seen = Set<String>()
    return processObjects.compactMap { process in
        guard isCapturingMicrophone(process),
              let bundle = processBundleID(process),
              seen.insert(bundle).inserted else { return nil }
        return ["app_bundle": bundle, "bundle": bundle]
    }
}

@available(macOS 14.4, *)
private func isCapturingMicrophone(_ process: AudioObjectID) -> Bool {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyIsRunningInput,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var running: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    return AudioObjectGetPropertyData(process, &address, 0, nil, &size, &running) == noErr && running != 0
}

@available(macOS 14.4, *)
private func processBundleID(_ process: AudioObjectID) -> String? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioProcessPropertyBundleID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var unmanaged: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    let status = withUnsafeMutablePointer(to: &unmanaged) {
        AudioObjectGetPropertyData(process, &address, 0, nil, &size, $0)
    }
    guard status == noErr, let value = unmanaged?.takeRetainedValue() as String?, !value.isEmpty else {
        return nil
    }
    return value
}
