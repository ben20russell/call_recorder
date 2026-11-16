import AppKit
import Foundation

private final class RecorderPopup: NSObject {
    private let panel: NSPanel
    private var timer: Timer?
    private var finished = false
    private var buttons: [NSButton] = []

    init(title: String, message: String, buttonTitles: [String]) {
        panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 360, height: 174),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false)
        super.init()

        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.hasShadow = true

        let effect = NSVisualEffectView(frame: panel.contentView?.bounds ?? .zero)
        effect.material = .hudWindow
        effect.blendingMode = .behindWindow
        effect.state = .active
        effect.autoresizingMask = [.width, .height]

        let heading = NSTextField(labelWithString: title)
        heading.font = .systemFont(ofSize: 14, weight: .semibold)
        heading.lineBreakMode = .byTruncatingTail
        let detail = NSTextField(wrappingLabelWithString: message)
        detail.font = .systemFont(ofSize: 12)
        detail.maximumNumberOfLines = 4

        let buttonRow = NSStackView()
        buttonRow.orientation = .horizontal
        buttonRow.alignment = .centerY
        buttonRow.distribution = .fill
        buttonRow.spacing = 8
        for (index, title) in buttonTitles.enumerated() {
            let button = NSButton(title: title, target: self, action: #selector(selected(_:)))
            button.tag = index
            button.bezelStyle = .rounded
            buttons.append(button)
            buttonRow.addArrangedSubview(button)
        }

        let contents = NSStackView(views: [heading, detail, buttonRow])
        contents.orientation = .vertical
        contents.alignment = .leading
        contents.distribution = .fill
        contents.spacing = 10
        contents.translatesAutoresizingMaskIntoConstraints = false
        effect.addSubview(contents)
        NSLayoutConstraint.activate([
            contents.leadingAnchor.constraint(equalTo: effect.leadingAnchor, constant: 16),
            contents.trailingAnchor.constraint(equalTo: effect.trailingAnchor, constant: -16),
            contents.topAnchor.constraint(equalTo: effect.topAnchor, constant: 16),
            contents.bottomAnchor.constraint(equalTo: effect.bottomAnchor, constant: -16),
            buttonRow.trailingAnchor.constraint(equalTo: contents.trailingAnchor),
        ])
        panel.contentView = effect
    }

    func show(timeout: TimeInterval, sound: Bool) {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1280, height: 800)
        let frame = panel.frame
        panel.setFrameOrigin(NSPoint(x: screen.maxX - frame.width - 18,
                                     y: screen.maxY - frame.height - 18))
        panel.orderFrontRegardless()
        if sound { NSSound(named: NSSound.Name("Glass"))?.play() }
        timer = Timer.scheduledTimer(withTimeInterval: max(1, timeout), repeats: false) { [weak self] _ in
            self?.finish("timeout")
        }
        DispatchQueue.global(qos: .utility).async { [weak self] in
            while let line = readLine() {
                if line.trimmingCharacters(in: .whitespacesAndNewlines) == "close" {
                    DispatchQueue.main.async { self?.finish("closed") }
                    return
                }
            }
            DispatchQueue.main.async { self?.finish("closed") }
        }
    }

    @objc private func selected(_ sender: NSButton) {
        guard buttons.indices.contains(sender.tag) else { return }
        finish(buttons[sender.tag].title)
    }

    private func finish(_ choice: String) {
        guard !finished else { return }
        finished = true
        timer?.invalidate()
        panel.close()
        emit(["choice": choice])
        NSApp.terminate(nil)
    }
}

func runPopup(_ args: [String]) {
    var title = "Call Recorder"
    var message = ""
    var timeout: TimeInterval = 600
    var buttonTitles: [String] = []
    var sound = false
    var index = 0
    while index < args.count {
        let option = args[index]
        index += 1
        switch option {
        case "--title", "--message", "--timeout", "--button":
            guard index < args.count else {
                emit(["event": "error", "message": "\(option) needs a value."])
                exit(2)
            }
            let value = args[index]
            index += 1
            switch option {
            case "--title": title = value
            case "--message": message = value
            case "--timeout": timeout = TimeInterval(value) ?? timeout
            default: buttonTitles.append(value)
            }
        case "--sound":
            sound = true
        default:
            emit(["event": "error", "message": "Unknown popup option: \(option)"])
            exit(2)
        }
    }

    if buttonTitles.isEmpty { buttonTitles = ["OK"] }
    NSApp.setActivationPolicy(.accessory)
    let popup = RecorderPopup(title: title, message: message, buttonTitles: buttonTitles)
    popup.show(timeout: timeout, sound: sound)
    NSApp.run()
}
