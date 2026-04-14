import AppKit
import CoreLocation
import CoreWLAN
import Foundation

struct NetworkInfo: Codable {
    let ssid: String
    let bssid: String
    let rssi: Int
    let noise: Int
    let channel: Int
    let channelBand: Int
    let channelWidth: Int
    let hidden: Bool
    let beaconInterval: Int
}

struct ScanResult: Codable {
    let timestamp: String
    let authorized: Bool
    let networks: [NetworkInfo]
    let totalCount: Int
    let hiddenCount: Int
    let error: String?
}

final class AppDelegate: NSObject, NSApplicationDelegate, CLLocationManagerDelegate {
    private let locationManager = CLLocationManager()
    private let outputURL = URL(fileURLWithPath: (FileManager.default.homeDirectoryForCurrentUser.path as NSString).appendingPathComponent(".wifi-monitor/native_scan.json"))

    func applicationDidFinishLaunching(_ notification: Notification) {
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyThreeKilometers
        startFlow()
    }

    private func startFlow() {
        let status = locationManager.authorizationStatus
        switch status {
        case .authorizedAlways, .authorizedWhenInUse:
            performScan()
        case .notDetermined:
            locationManager.requestWhenInUseAuthorization()
        case .restricted, .denied:
            writeResult(ScanResult(timestamp: Self.timestamp(), authorized: false, networks: [], totalCount: 0, hiddenCount: 0, error: "Location permission denied. Open System Settings -> Privacy & Security -> Location Services and allow this app."))
        @unknown default:
            writeResult(ScanResult(timestamp: Self.timestamp(), authorized: false, networks: [], totalCount: 0, hiddenCount: 0, error: "Unknown location authorization state."))
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        startFlow()
    }

    private func performScan() {
        let client = CWWiFiClient.shared()
        guard let iface = client.interface() else {
            writeResult(ScanResult(timestamp: Self.timestamp(), authorized: true, networks: [], totalCount: 0, hiddenCount: 0, error: "No WiFi interface."))
            return
        }
        do {
            let scanned = try iface.scanForNetworks(withName: nil)
            var networks: [NetworkInfo] = []
            for n in scanned {
                networks.append(NetworkInfo(
                    ssid: n.ssid ?? "",
                    bssid: n.bssid ?? "",
                    rssi: n.rssiValue,
                    noise: n.noiseMeasurement,
                    channel: n.wlanChannel?.channelNumber ?? 0,
                    channelBand: Int(n.wlanChannel?.channelBand.rawValue ?? 0),
                    channelWidth: Int(n.wlanChannel?.channelWidth.rawValue ?? 0),
                    hidden: (n.ssid == nil || n.ssid == ""),
                    beaconInterval: n.beaconInterval
                ))
            }
            let hidden = networks.filter { $0.hidden }.count
            writeResult(ScanResult(timestamp: Self.timestamp(), authorized: true, networks: networks, totalCount: networks.count, hiddenCount: hidden, error: nil))
        } catch {
            writeResult(ScanResult(timestamp: Self.timestamp(), authorized: true, networks: [], totalCount: 0, hiddenCount: 0, error: "Scan error: \(error.localizedDescription)"))
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in
            self?.performScan()
        }
    }

    private func writeResult(_ result: ScanResult) {
        let dir = outputURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(result) {
            try? data.write(to: outputURL, options: .atomic)
        }
    }

    static func timestamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f.string(from: Date())
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.setActivationPolicy(.accessory)
app.delegate = delegate
app.run()
