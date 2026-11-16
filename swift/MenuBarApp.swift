import AppKit
import ServiceManagement
import SwiftUI
import WebKit

private let recorderURL = URL(string: "http://127.0.0.1:8765")!

@main
struct CallRecorderApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        MenuBarExtra("Call Recorder", systemImage: "record.circle") {
            MenuContent()
        }
        .menuBarExtraStyle(.menu)

        Window("Call Recorder", id: "recorder") {
            RecorderWindow()
        }
        .defaultSize(width: 1100, height: 760)
    }
}

private struct MenuContent: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Open Call Recorder") {
            openWindow(id: "recorder")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("Open in Browser") {
            NSWorkspace.shared.open(recorderURL)
        }
        Divider()
        Button("Quit Call Recorder") {
            NSApp.terminate(nil)
        }
    }
}

private struct RecorderWindow: View {
    var body: some View {
        RecorderWebView()
            .frame(minWidth: 720, minHeight: 520)
    }
}

private struct RecorderWebView: NSViewRepresentable {
    func makeNSView(context: Context) -> WKWebView {
        let view = WKWebView()
        view.load(URLRequest(url: recorderURL))
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private var server: Process?
    private var logFile: FileHandle?

    func applicationDidFinishLaunching(_ notification: Notification) {
        startServer()
        do {
            try SMAppService.mainApp.register()
        } catch {
            NSLog("Call Recorder could not register for login: %@", error.localizedDescription)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let server, server.isRunning {
            server.terminate()
        }
        try? logFile?.close()
    }

    private func startServer() {
        guard let root = Bundle.main.infoDictionary?["CALLREC_ROOT"] as? String else {
            NSLog("CALLREC_ROOT is missing from the app bundle")
            return
        }

        let logURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/CallRecorder.log")
        do {
            try FileManager.default.createDirectory(
                at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil)
            }
            logFile = try FileHandle(forWritingTo: logURL)
            try logFile?.seekToEnd()
        } catch {
            NSLog("Call Recorder could not open its log: %@", error.localizedDescription)
        }

        let process = Process()
        let virtualEnvironmentPython = URL(fileURLWithPath: root).appendingPathComponent(".venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: virtualEnvironmentPython.path) {
            process.executableURL = virtualEnvironmentPython
            process.arguments = ["\(root)/callrec", "ui", "--no-browser"]
        } else {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            process.arguments = ["python3", "\(root)/callrec", "ui", "--no-browser"]
        }
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = root
        process.environment = environment
        process.standardOutput = logFile
        process.standardError = logFile
        do {
            try process.run()
            server = process
        } catch {
            NSLog("Call Recorder could not start its local server: %@", error.localizedDescription)
        }
    }
}
