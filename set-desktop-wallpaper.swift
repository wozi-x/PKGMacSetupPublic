import AppKit
import SystemConfiguration
import Darwin

// Use AppKit directly: no Apple Events, Finder control, or private wallpaper DB.
func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("wallpaper: \(message)\n".utf8))
    exit(1)
}

let args = Array(CommandLine.arguments.dropFirst())
guard args.count == 2, ["--check", "--apply"].contains(args[1]), args[0].hasPrefix("/") else {
    fail("usage: swift set-desktop-wallpaper.swift /absolute/image/path --check|--apply")
}
var consoleUID: uid_t = 0
let consoleUser = SCDynamicStoreCopyConsoleUser(nil, &consoleUID, nil) as String?
guard let user = consoleUser, user != "loginwindow", consoleUID != 0, consoleUID == getuid() else {
    print("wallpaper: skipped (run as the logged-in console user)")
    exit(0)
}
let screens = NSScreen.screens
guard !screens.isEmpty else {
    print("wallpaper: skipped (no accessible graphical display)")
    exit(0)
}
let target = URL(fileURLWithPath: args[0]).standardizedFileURL
guard FileManager.default.isReadableFile(atPath: target.path), NSImage(contentsOf: target) != nil else {
    fail("image is missing or unreadable: \(target.path)")
}
let workspace = NSWorkspace.shared
let pending = screens.filter { workspace.desktopImageURL(for: $0)?.standardizedFileURL != target }
guard !pending.isEmpty else {
    print("wallpaper: unchanged")
    exit(0)
}
if args[1] == "--check" {
    print("wallpaper: would-change (\(pending.count) display(s))")
    exit(0)
}
for screen in pending {
    do {
        try workspace.setDesktopImageURL(target, for: screen, options: [:])
    } catch {
        fail("could not set desktop image: \(error.localizedDescription)")
    }
    // WallpaperAgent publishes the new URL asynchronously after the setter returns.
    let deadline = Date().addingTimeInterval(5)
    while workspace.desktopImageURL(for: screen)?.standardizedFileURL != target && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }
    guard workspace.desktopImageURL(for: screen)?.standardizedFileURL == target else {
        fail("desktop image verification failed")
    }
}
print("wallpaper: changed (\(pending.count) display(s))")
